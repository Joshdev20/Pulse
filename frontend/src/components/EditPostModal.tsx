import { useEffect, useState } from 'react'
import { Modal } from './UI'
import { api } from '../lib/api'
import { useToast } from '../context/ToastContext'
import type { Post } from '../lib/types'

const MAX_LENGTH = 2000

export function EditPostModal({
  post,
  onClose,
  onSaved,
}: {
  post: Post
  onClose: () => void
  onSaved: (post: Post) => void
}) {
  const { notify } = useToast()
  const [body, setBody] = useState(post.body)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setBody(post.body), [post])

  const remaining = MAX_LENGTH - body.length
  const canSave = body.trim().length > 0 && remaining >= 0 && !busy && body !== post.body

  async function save() {
    if (!canSave) return
    setBusy(true)
    setError(null)
    try {
      const updated = await api.updatePost(post.id, body.trim())
      notify('Post updated')
      onSaved(updated)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save changes')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Edit post"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={!canSave} onClick={save}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </>
      }
    >
      {error && <div className="form-error" style={{ marginBottom: 12 }}>{error}</div>}
      <textarea
        className="textarea"
        value={body}
        autoFocus
        maxLength={MAX_LENGTH + 200}
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
            event.preventDefault()
            void save()
          }
        }}
      />
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
        <span className={`char-count${remaining < 0 ? ' over' : remaining < 100 ? ' warn' : ''}`}>
          {remaining} characters left
        </span>
      </div>
    </Modal>
  )
}
