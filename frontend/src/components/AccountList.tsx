import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Avatar } from './Avatar'
import { CheckIcon, CloseIcon, PlusIcon } from './Icons'
import { Spinner } from './UI'
import { useAuth } from '../context/AuthContext'

/**
 * The saved-account list: switch to one, forget one, or add another.
 *
 * Rendered in both the sidebar switcher and the profile menu, so the two
 * surfaces cannot drift apart. `onClose` lets the caller shut its own menu —
 * on a successful switch, and before routing to the add-account screen.
 */
export function AccountList({ onClose }: { onClose: () => void }) {
  const { user, accounts, activeAccountId, switchAccount, forgetAccount } = useAuth()
  const navigate = useNavigate()
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (!user || accounts.length === 0) return null

  const others = accounts.filter((account) => account.id !== activeAccountId)

  /**
   * Swap to `id`, keeping the menu open until the switch settles.
   *
   * The menu must stay open on failure: `error` is rendered inside it, so
   * closing first would hide the only explanation the user gets. It closes only
   * once the new session is live.
   */
  async function choose(id: number) {
    setError(null)
    setBusyId(id)
    try {
      await switchAccount(id)
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not switch account')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <>
      {accounts.map((account) => {
        const active = account.id === activeAccountId
        // The active row is rendered from the live session rather than the stored
        // snapshot, so a name or avatar changed in settings shows here immediately
        // instead of after the next sign-in.
        const shown =
          active && user
            ? {
                ...account,
                display_name: user.display_name,
                username: user.username,
                avatar_color: user.avatar_color,
                avatar_url: user.avatar_url,
              }
            : account
        return (
          <div key={account.id} className={`account-row${active ? ' active' : ''}`}>
            <button
              type="button"
              className="account-row-main"
              disabled={active || busyId !== null}
              onClick={() => void choose(account.id)}
            >
              <Avatar
                name={shown.display_name}
                color={shown.avatar_color}
                url={shown.avatar_url}
                size="sm"
              />
              <span className="account-row-info">
                <span className="account-row-name">{shown.display_name}</span>
                <span className="account-row-handle">@{shown.username}</span>
              </span>
              {active && <CheckIcon size={15} />}
              {busyId === account.id && <Spinner size={14} />}
            </button>
            {!active && (
              <button
                type="button"
                className="account-row-forget"
                onClick={() => forgetAccount(account.id)}
                aria-label={`Forget @${account.username}`}
                title="Remove from this device"
              >
                <CloseIcon size={13} />
              </button>
            )}
          </div>
        )
      })}

      {error && <p className="field-error account-error">{error}</p>}

      <button
        type="button"
        className="account-add"
        onClick={() => {
          onClose()
          // `add=1` puts the sign-in screen in add-account mode, so the current
          // session is kept and the new account is added to it rather than
          // replacing it.
          navigate('/login?add=1')
        }}
      >
        <PlusIcon size={16} /> Add account
      </button>
      {others.length > 0 && (
        <p className="account-hint">
          {others.length} other {others.length === 1 ? 'account' : 'accounts'} on this device
        </p>
      )}
    </>
  )
}
