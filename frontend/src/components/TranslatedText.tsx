import { useState } from 'react'
import { api } from '../lib/api'
import { PostText } from './PostCard'
import { TranslateIcon } from './Icons'
import type { Post } from '../lib/types'

interface TranslatedTextProps {
  post: Post
  className?: string
}

/**
 * A post body with a "Translate" affordance, shown only when the server has
 * already decided the post is in a language other than the reader's.
 *
 * The decision is the server's on purpose. Detection runs there against a
 * pinned seed, so the same post never flickers between "translate" and "not",
 * and the client never has to guess at language from a short string — a guess
 * that is confidently wrong for text like "ok" or "nice one".
 *
 * The translation itself is fetched lazily, on tap. The service is best-effort
 * and free, so a failure surfaces as a quiet message next to the toggle rather
 * than an error state that replaces the post: the original is always readable.
 */
export function TranslatedText({ post, className }: TranslatedTextProps) {
  const [translated, setTranslated] = useState<string | null>(null)
  const [showing, setShowing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  const language = post.language_name

  async function translate() {
    if (translated) {
      setShowing(true)
      return
    }
    setBusy(true)
    setFailed(false)
    try {
      const result = await api.translatePost(post.id)
      if (result.translated) {
        setTranslated(result.translated)
        setShowing(true)
      } else {
        // The service returned nothing usable. There is no toggle to show, so
        // the post stays as it is.
        setFailed(true)
      }
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {showing && translated ? (
        <p className={`${className ?? ''} post-translated`.trim()}>{translated}</p>
      ) : (
        <PostText body={post.body} className={className} />
      )}

      {(language || translated) && (
        <div className="translate-row">
          {translated ? (
            <button
              type="button"
              className="translate-toggle"
              onClick={(event) => {
                event.stopPropagation()
                setShowing((current) => !current)
              }}
            >
              {showing ? 'Show original' : `Show translation`}
            </button>
          ) : (
            <button
              type="button"
              className="translate-toggle"
              onClick={(event) => {
                event.stopPropagation()
                void translate()
              }}
              disabled={busy}
            >
              <TranslateIcon size={14} />
              {busy ? 'Translating…' : `Translate from ${language ?? 'another language'}`}
            </button>
          )}
          {failed && <span className="translate-error">Translation unavailable</span>}
        </div>
      )}
    </>
  )
}
