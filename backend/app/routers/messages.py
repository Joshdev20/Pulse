"""Direct messages: conversations, message history and read state."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from ..deps import CurrentUser, DbSession, serialize_media, serialize_user, serialize_users
from ..models import Conversation, ConversationParticipant, Media, Message, User, utcnow
from ..realtime import manager
from ..schemas import ConversationOut, ConversationStart, MessageCreate, MessageOut

router = APIRouter(prefix="/api/conversations", tags=["messages"])

MAX_PAGE_SIZE = 100


def serialize_messages(db: Session, messages: list[Message], viewer: User | None) -> list[MessageOut]:
    if not messages:
        return []
    authors = serialize_users(db, [m.sender for m in messages], viewer)
    return [
        MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            body=m.body,
            created_at=m.created_at,
            sender=authors[m.sender_id],
            media=serialize_media(m.media),
            mine=viewer is not None and m.sender_id == viewer.id,
        )
        for m in messages
    ]


# A conversation summary needs one row per conversation; this module does it
# with a handful of set-based queries instead of a query per conversation,
# which matters because every one of those is a round-trip to the database host.

# Unread messages for a participant row: everything from the other side newer
# than the last read, or all of it when the thread has never been opened.
_UNREAD_FOR_PARTICIPANT = (
    select(func.count(Message.id))
    .where(
        Message.conversation_id == ConversationParticipant.conversation_id,
        Message.sender_id != ConversationParticipant.user_id,
        or_(
            ConversationParticipant.last_read_at.is_(None),
            Message.created_at > ConversationParticipant.last_read_at,
        ),
    )
    .correlate(ConversationParticipant)
    .scalar_subquery()
)


def _summarize(db: Session, user: User) -> list[ConversationOut]:
    """Build the inbox for ``user`` with a constant number of queries."""
    # The viewer's own participant row drives the filter and the unread count;
    # the partner is a second, aliased participant row so the two do not fight
    # over a single join.
    partner_cp = aliased(ConversationParticipant)
    person = aliased(User)
    last_id = (
        select(func.max(Message.id))
        .where(Message.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )

    rows = db.execute(
        select(Conversation, person, Message, _UNREAD_FOR_PARTICIPANT.label("unread_count"))
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .join(partner_cp, partner_cp.conversation_id == Conversation.id)
        .join(person, person.id == partner_cp.user_id)
        .outerjoin(Message, Message.id == last_id)
        .where(
            ConversationParticipant.user_id == user.id,
            partner_cp.user_id != user.id,
        )
        .order_by(Conversation.last_message_at.desc())
    ).all()

    # The partner profiles carry follower and post counts, so serialize them in
    # one batch rather than relying on the per-user counts on UserPublic.
    serialized_partners = serialize_users(db, [row[1] for row in rows], user)
    senders = serialize_users(db, [row[2].sender for row in rows if row[2] is not None], user)

    out: list[ConversationOut] = []
    for conversation, partner, last, unread_count in rows:
        preview = None
        if last is not None:
            preview = MessageOut(
                id=last.id,
                conversation_id=last.conversation_id,
                body=last.body,
                created_at=last.created_at,
                sender=senders[last.sender_id],
                media=serialize_media(last.media),
                mine=last.sender_id == user.id,
            )
        out.append(
            ConversationOut(
                id=conversation.id,
                created_at=conversation.created_at,
                last_message_at=conversation.last_message_at,
                partner=serialized_partners[partner.id],
                last_message=preview,
                unread_count=unread_count or 0,
            )
        )
    return out


def _participant(db: Session, conversation_id: int, user_id: int) -> ConversationParticipant | None:
    return db.scalar(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
    )


def _require_participant(db: Session, conversation_id: int, user: User) -> ConversationParticipant:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    participant = _participant(db, conversation_id, user.id)
    if participant is None:
        # 404 rather than 403 so the existence of a private thread is not revealed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return participant


def _partner_of(db: Session, conversation_id: int, viewer_id: int) -> User:
    row = db.execute(
        select(User)
        .join(ConversationParticipant, ConversationParticipant.user_id == User.id)
        .where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id != viewer_id,
        )
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return row[0]


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: DbSession, user: CurrentUser) -> list[ConversationOut]:
    """The inbox, newest activity first, with a preview and unread count."""
    return _summarize(db, user)


@router.get("/unread-count")
def unread_total(db: DbSession, user: CurrentUser) -> dict[str, int]:
    total = db.scalar(
        select(func.coalesce(func.sum(_UNREAD_FOR_PARTICIPANT), 0)).where(
            ConversationParticipant.user_id == user.id
        )
    )
    return {"count": int(total or 0)}


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def start_conversation(
    payload: ConversationStart, db: DbSession, user: CurrentUser
) -> ConversationOut:
    """Open a 1:1 conversation with ``username``, reusing an existing thread."""
    username = payload.username.strip().lower()
    partner = db.scalar(
        select(User).where(func.lower(User.username) == username, User.is_active.is_(True))
    )
    if partner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if partner.id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot message yourself")

    existing = db.scalar(
        select(Conversation)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .where(ConversationParticipant.user_id == user.id)
        .where(
            Conversation.id.in_(
                select(ConversationParticipant.conversation_id).where(
                    ConversationParticipant.user_id == partner.id
                )
            )
        )
        .limit(1)
    )
    if existing is None:
        existing = Conversation()
        db.add(existing)
        db.flush()
        db.add_all(
            [
                ConversationParticipant(conversation_id=existing.id, user_id=user.id),
                ConversationParticipant(conversation_id=existing.id, user_id=partner.id),
            ]
        )
        db.commit()
        db.refresh(existing)

    return ConversationOut(
        id=existing.id,
        created_at=existing.created_at,
        last_message_at=existing.last_message_at,
        partner=serialize_user(db, partner, user),
        last_message=None,
        unread_count=0,
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: int,
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    before_id: int | None = Query(None, ge=1),
) -> list[MessageOut]:
    """Newest-last page of history, for scrolling back through a thread."""
    _require_participant(db, conversation_id, user)

    query = select(Message).where(Message.conversation_id == conversation_id)
    if before_id is not None:
        query = query.where(Message.id < before_id)
    messages = list(
        db.scalars(query.order_by(Message.id.desc()).limit(limit)).all()
    )
    messages.reverse()
    return serialize_messages(db, messages, user)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: int, payload: MessageCreate, db: DbSession, user: CurrentUser
) -> MessageOut:
    _require_participant(db, conversation_id, user)

    body = payload.body.strip()
    attachments = _claim_media(db, payload.media_ids, user)
    if not body and not attachments:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message cannot be empty")

    message = Message(conversation_id=conversation_id, sender_id=user.id, body=body)
    db.add(message)
    db.flush()
    for item in attachments:
        item.message_id = message.id

    conversation = db.get(Conversation, conversation_id)
    conversation.last_message_at = message.created_at or utcnow()
    # The sender has implicitly read their own message.
    participant = _participant(db, conversation_id, user.id)
    participant.last_read_at = utcnow()
    db.commit()
    db.refresh(message)

    serialized = serialize_messages(db, [message], user)[0]
    # Every participant gets its own copy: ``mine`` is relative to the reader,
    # so one serialization cannot be broadcast to everyone. This also keeps the
    # sender's other open tabs in sync, not just the recipient's.
    participant_ids = set(
        db.scalars(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == conversation_id
            )
        ).all()
    )
    for participant_id in participant_ids:
        viewer = db.get(User, participant_id)
        if viewer is None:
            continue
        payload = {
            "type": "message",
            "conversation_id": conversation_id,
            "message": serialize_messages(db, [message], viewer)[0].model_dump(mode="json"),
        }
        await manager.publish(participant_id, payload)
    return serialized


def _claim_media(db: Session, media_ids: list[int], user: User) -> list[Media]:
    """Attach the caller's own unlinked uploads, refusing anything else."""
    if not media_ids:
        return []
    unique = list(dict.fromkeys(media_ids))
    items = list(db.scalars(select(Media).where(Media.id.in_(unique))).all())
    if len(items) != len(unique):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown attachment")
    for item in items:
        if item.owner_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot attach this file")
        if item.post_id is not None or item.message_id is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Attachment already in use")
    return items


@router.post("/{conversation_id}/read")
def mark_read(conversation_id: int, db: DbSession, user: CurrentUser) -> dict[str, int]:
    participant = _require_participant(db, conversation_id, user)
    participant.last_read_at = utcnow()
    db.commit()
    return {"unread_count": 0}


@router.post("/{conversation_id}/typing", status_code=status.HTTP_202_ACCEPTED)
async def typing(conversation_id: int, db: DbSession, user: CurrentUser) -> dict[str, bool]:
    """Relay a typing indicator to the other participant."""
    _require_participant(db, conversation_id, user)
    partner = _partner_of(db, conversation_id, user.id)
    await manager.publish(
        partner.id,
        {"type": "typing", "conversation_id": conversation_id, "user_id": user.id},
    )
    return {"ok": True}
