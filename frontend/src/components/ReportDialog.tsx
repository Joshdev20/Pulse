import { useState } from 'react'
import { Modal } from './UI'
import { WarningIcon } from './Icons'
import { api } from '../lib/api'
import { useToast } from '../context/ToastContext'

/** The complaint codes the API accepts, with the wording a reader sees. */
export const REPORT_REASONS = [
  { code: 'spam', label: 'Spam or unwanted advertising' },
  { code: 'harassment', label: 'Harassment or hate' },
  { code: 'misleading', label: 'Misleading or false information' },
  { code: 'explicit', label: 'Sensitive or explicit content' },
  { code: 'other', label: 'Something else' },
] as const

/**
 * Ask a reader what is wrong with a post, then file the complaint.
 *
 * One tap to a reason and one to send: a report form that demands typing is a
 * report nobody files. The note is optional, and the outcome is deliberately
 * quiet — the reporter is thanked, and the author is never told.
 */
export function ReportDialog({ postId, onClose }: { postId: number; onClose: () => void }) {
  const { notify } = useToast()
  const [reason, setReason] = useState<string>('spam')
  const [details, setDetails] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await api.reportPost(postId, reason, details.trim())
      notify('Thanks — this post has been sent for review')
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not send the report')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Report post"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? 'Sending…' : 'Send report'}
          </button>
        </>
      }
    >
      <div className="report-intro">
        <WarningIcon size={18} />
        <p>Tell us what is wrong. Your report is private — the author is not told who sent it.</p>
      </div>

      <div className="report-reasons" role="radiogroup" aria-label="Reason">
        {REPORT_REASONS.map((option) => (
          <button
            key={option.code}
            type="button"
            role="radio"
            aria-checked={reason === option.code}
            className={`report-reason${reason === option.code ? ' selected' : ''}`}
            onClick={() => setReason(option.code)}
          >
            <span className="report-radio" aria-hidden="true" />
            {option.label}
          </button>
        ))}
      </div>

      <label className="report-note">
        <span>Anything else we should know? (optional)</span>
        <textarea
          className="textarea"
          value={details}
          maxLength={500}
          rows={3}
          placeholder="Add context for the moderator"
          onChange={(event) => setDetails(event.target.value)}
        />
      </label>

      {error && <div className="form-error">{error}</div>}
    </Modal>
  )
}
