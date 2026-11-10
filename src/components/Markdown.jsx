import { Fragment } from 'react'
import { parseMarkdown } from '../replay.js'

function Inline({ nodes }) {
  return nodes.map((n, i) => {
    if (n.type === 'strong') return <strong key={i}>{n.text}</strong>
    if (n.type === 'em') return <em key={i}>{n.text}</em>
    if (n.type === 'code') return <code key={i}>{n.text}</code>
    return <Fragment key={i}>{n.text}</Fragment>
  })
}

/** Renders the summary report's Markdown as React elements. Never uses innerHTML. */
export default function Markdown({ source }) {
  const blocks = parseMarkdown(source)
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.type === 'heading') {
          // The page owns h1 and h2; headings inside the preview start at h3.
          const Tag = `h${Math.min(6, b.level + 2)}`
          return <Tag key={i}><Inline nodes={b.inline} /></Tag>
        }
        if (b.type === 'list') {
          return <ul key={i}>{b.items.map((item, j) => <li key={j}><Inline nodes={item} /></li>)}</ul>
        }
        if (b.type === 'table') {
          return (
            <div className="table-scroll" key={i}>
              <table className="data-table stack-table">
                <thead><tr>{b.head.map((c, j) => <th key={j} scope="col"><Inline nodes={c} /></th>)}</tr></thead>
                <tbody>
                  {b.rows.map((r, j) => (
                    <tr key={j}>
                      {r.map((c, k) => <td key={k} data-label={b.head[k]?.map((n) => n.text).join('')}><Inline nodes={c} /></td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
        return <p key={i}><Inline nodes={b.inline} /></p>
      })}
    </div>
  )
}
