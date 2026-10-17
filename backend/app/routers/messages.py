"""Direct messages: conversations, message history and read state."""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from ..deps import (
    CurrentUser,
    DbSession,
    serialize_media,
    serialize_user,
    serialize_users,
)
from ..models import (
    Conversation,
    ConversationParticipant,
    Media,
    Message,
    MessageLike,
    User,
    utcnow,
)
from .. import push
from ..realtime import manager
from ..schemas import (
    ConversationOut,
    ConversationStart,
    MessageCreate,
    MessageOut,
    MessageQuote,
)

router = APIRouter(prefix="/api/conversations", tags=["messages"])

MAX_PAGE_SIZE = 100


def _quotes_for(db: Session, messages: list[Message]) -> dict[int, MessageQuote]:
    """Resolve the messages that these messages reply to, in two queries.

    Returned as a map from the *quoting* message id to its quote, so the caller
    does not have to walk the relationship (which would be one query per reply).
    A reply whose target no longer exists is simply absent from the map and
    serializes without a quote rather than failing.
    """
    target_ids = {m.reply_to_id for m in messages if m.reply_to_id is not None}
    if not target_ids:
        return {}

    targets = list(db.scalars(select(Message).where(Message.id.in_(target_ids))).all())
    if not targets:
        return {}

    senders = {
        user.id: user
        for user in db.scalars(
            select(User).where(User.id.in_({t.sender_id for t in targets}))
        ).all()
    }

    by_id = {t.id: t for t in targets}
    quotes: dict[int, MessageQuote] = {}
    for message in messages:
        if message.reply_to_id is None:
            continue
        target = by_id.get(message.reply_to_id)
        if target is None:
            continue
        sender = senders.get(target.sender_id)
        quotes[message.id] = MessageQuote(
            id=target.id,
            body=target.body,
            sender_username=sender.username if sender else "",
            sender_display_name=sender.display_name if sender else "",
            has_media=bool(target.media),
        )
    return quotes


def _like_state(
    db: Session, messages: list[Message], viewer: User | None
) -> tuple[dict[int, int], set[int]]:
    """Batch the like totals and the viewer's own likes for a set of messages."""
    if not messages:
        return {}, set()
    ids = [m.id for m in messages]
    counts = dict(
        db.execute(
            select(MessageLike.message_id, func.count(MessageLike.id))
            .where(MessageLike.message_id.in_(ids))
            .group_by(MessageLike.message_id)
        ).all()
    )
    mine: set[int] = set()
    if viewer is not None:
        mine = {
            row[0]
            for row in db.execute(
                select(MessageLike.message_id).where(
                    MessageLike.user_id == viewer.id, MessageLike.message_id.in_(ids)
                )
            ).all()
        }
    return counts, mine


def serialize_messages(db: Session, messages: list[Message], viewer: User | None) -> list[MessageOut]:
    if not messages:
        return []
    authors = serialize_users(db, [m.sender for m in messages], viewer)
    quotes = _quotes_for(db, messages)
    like_counts, liked_ids = _like_state(db, messages, viewer)
    return [
        MessageOut(
            id=m.id,
            conversation_id=m.conversation_id,
            body=m.body,
            created_at=m.created_at,
            sender=authors[m.sender_id],
            media=serialize_media(m.media),
            mine=viewer is not None and m.sender_id == viewer.id,
            reply_to=quotes.get(m.id),
            like_count=like_counts.get(m.id, 0),
            liked_by_me=m.id in liked_ids,
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
    serialized_partners = serialize_users(
        db, [row[1] for row in rows], user, with_presence=True
    )
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
        partner=serialize_user(db, partner, user, with_presence=True),
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

    reply_to_id = _validate_reply_target(db, conversation_id, payload.reply_to_id)

    message = Message(
        conversation_id=conversation_id,
        sender_id=user.id,
        body=body,
        reply_to_id=reply_to_id,
    )
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
        # Web Push is the only channel that reaches a closed browser; the
        # socket above covers an open tab. The sender is skipped: they already
        # know what they wrote.
        if participant_id != user.id:
            push.schedule_notify(participant_id, push.message_payload(db, message, conversation_id))
    return serialized


def _validate_reply_target(
    db: Session, conversation_id: int, reply_to_id: int | None
) -> int | None:
    """Check a swipe-to-reply target, returning the id to store or None.

    The target must be a message in *this* conversation. Without that check a
    client could quote a message from a thread it is not in and have the quote
    echoed back to participants here, which would leak another conversation.
    """
    if reply_to_id is None:
        return None
    target = db.get(Message, reply_to_id)
    if target is None or target.conversation_id != conversation_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot reply to that message")
    return reply_to_id


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


def _require_message(db: Session, conversation_id: int, message_id: int, user: User) -> Message:
    """Fetch a message in a thread the caller belongs to, or raise 404."""
    _require_participant(db, conversation_id, user)
    message = db.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    return message


@router.post("/{conversation_id}/messages/{message_id}/like")
async def like_message(
    conversation_id: int, message_id: int, db: DbSession, user: CurrentUser
) -> dict[str, int | bool]:
    """Double-tap a message: like it, or leave the existing like alone.

    Idempotent. A double-tap that races itself would otherwise hit the unique
    constraint on the second insert; the caller's intent is already satisfied,
    so that is treated as success rather than a 500.
    """
    message = _require_message(db, conversation_id, message_id, user)
    already = db.scalar(
        select(MessageLike.id).where(
            MessageLike.user_id == user.id, MessageLike.message_id == message_id
        )
    )
    if already is None:
        db.add(MessageLike(user_id=user.id, message_id=message_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    count = db.scalar(
        select(func.count(MessageLike.id)).where(MessageLike.message_id == message_id)
    )
    await _broadcast_message_like(db, message, count or 0)
    return {"liked_by_me": True, "like_count": count or 0}


@router.delete("/{conversation_id}/messages/{message_id}/like")
async def unlike_message(
    conversation_id: int, message_id: int, db: DbSession, user: CurrentUser
) -> dict[str, int | bool]:
    """Undo a like. Idempotent: removing a like that is not there is fine."""
    message = _require_message(db, conversation_id, message_id, user)
    db.execute(
        delete(MessageLike).where(
            MessageLike.user_id == user.id, MessageLike.message_id == message_id
        )
    )
    db.commit()
    count = db.scalar(
        select(func.count(MessageLike.id)).where(MessageLike.message_id == message_id)
    )
    await _broadcast_message_like(db, message, count or 0)
    return {"liked_by_me": False, "like_count": count or 0}


async def _broadcast_message_like(db: Session, message: Message, count: int) -> None:
    """Tell both participants the new like total for a message.

    Sent as its own frame rather than a whole message: the recipient may not
    have the message loaded, and re-sending it would fight with the client's
    list. ``liked_by_me`` is per-recipient, so it is derived on the client from
    its own optimistic state.
    """
    participant_ids = set(
        db.scalars(
            select(ConversationParticipant.user_id).where(
                ConversationParticipant.conversation_id == message.conversation_id
            )
        ).all()
    )
    payload = {
        "type": "message_like",
        "conversation_id": message.conversation_id,
        "message_id": message.id,
        "like_count": count,
    }
    for participant_id in participant_ids:
        await manager.publish(participant_id, payload)
