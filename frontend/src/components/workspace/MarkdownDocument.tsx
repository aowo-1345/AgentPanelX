import { Children, isValidElement, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MermaidDiagram } from '@/components/workspace/MermaidDiagram';

interface CodeElementProps {
  children?: ReactNode;
  className?: string;
}

export function MarkdownDocument({ content }: { content: string }) {
  return (
    <div className="markdown-document">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ children, ...props }) {
            return (
              <a {...props} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          pre({ children }) {
            const child = Children.count(children) === 1 ? Children.only(children) : null;
            if (isValidElement<CodeElementProps>(child)) {
              const language = /language-([^\s]+)/.exec(child.props.className ?? '')?.[1];
              if (language === 'mermaid') {
                return (
                  <MermaidDiagram
                    source={String(child.props.children ?? '').replace(/\n$/, '')}
                  />
                );
              }
            }
            return <pre>{children}</pre>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
