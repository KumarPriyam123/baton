import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** User content as Markdown, never as HTML: no rehype-raw, so `<script>` is shown as text (I12). */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown text-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
