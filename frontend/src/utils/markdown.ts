import DOMPurify from 'dompurify'
import { marked } from 'marked'

const allowedSchemes = new Set(['http:', 'https:', 'mailto:'])

function secureLinks(container: ParentNode) {
  container.querySelectorAll('a').forEach((link) => {
    const rawHref = link.getAttribute('href') || ''
    const isLocal = rawHref.startsWith('/') || rawHref.startsWith('#')
    if (!isLocal) {
      try {
        const url = new URL(rawHref, window.location.origin)
        if (!allowedSchemes.has(url.protocol)) link.removeAttribute('href')
        else if (url.origin !== window.location.origin) {
          link.setAttribute('target', '_blank')
          link.setAttribute('rel', 'noopener noreferrer')
        }
      } catch {
        link.removeAttribute('href')
      }
    }
  })
}

export function renderSafeMarkdown(content: string): string {
  const rendered = marked.parse(content, { async: false }) as string
  const sanitized = DOMPurify.sanitize(rendered, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'svg', 'math', 'form'],
    FORBID_ATTR: ['style', 'srcdoc'],
  })
  const template = document.createElement('template')
  template.innerHTML = sanitized
  secureLinks(template.content)
  return template.innerHTML
}
