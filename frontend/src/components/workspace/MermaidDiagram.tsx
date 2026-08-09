import { useEffect, useState } from 'react';

interface MermaidDiagramProps {
  source: string;
}

interface MermaidRenderState {
  svg: string | null;
  error: string | null;
}

let diagramSequence = 0;
let mermaidPromise: Promise<typeof import('mermaid')['default']> | null = null;

function loadMermaid() {
  if (mermaidPromise === null) {
    mermaidPromise = import('mermaid').then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'dark',
        flowchart: { useMaxWidth: true },
      });
      return mermaid;
    });
  }
  return mermaidPromise;
}

export function MermaidDiagram({ source }: MermaidDiagramProps) {
  const [renderState, setRenderState] = useState<MermaidRenderState>({
    svg: null,
    error: null,
  });

  useEffect(() => {
    let active = true;
    const diagramId = `plan-mermaid-${diagramSequence++}`;

    setRenderState({ svg: null, error: null });

    void loadMermaid()
      .then((mermaid) => mermaid.render(diagramId, source))
      .then(({ svg }) => {
        if (active) setRenderState({ svg, error: null });
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setRenderState({
          svg: null,
          error: caught instanceof Error ? caught.message : 'Mermaid could not render this diagram.',
        });
      });

    return () => {
      active = false;
    };
  }, [source]);

  if (renderState.error) {
    return (
      <div className="mermaid-fallback" role="alert">
        <p>Diagram preview unavailable: {renderState.error}</p>
        <pre>
          <code>{source}</code>
        </pre>
      </div>
    );
  }

  if (renderState.svg === null) {
    return <div className="mermaid-loading">Rendering diagram…</div>;
  }

  return (
    <div
      className="mermaid-diagram"
      // Mermaid emits an SVG from the diagram source after applying strict security rules.
      dangerouslySetInnerHTML={{ __html: renderState.svg }}
    />
  );
}
