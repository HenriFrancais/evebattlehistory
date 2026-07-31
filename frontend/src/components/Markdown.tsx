import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// react-markdown renders to React elements and, without rehype-raw, ignores any
// embedded raw HTML — so user-authored AAR/comment text cannot inject markup or
// scripts. We keep the surface tight: images are disallowed and links are forced
// to open safely in a new tab.
const COMPONENTS: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow ugc">
      {children}
    </a>
  ),
}

/** Render trusted-source-free markdown safely. Used for the AAR body + comments. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        disallowedElements={['img']}
        unwrapDisallowed
        components={COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
