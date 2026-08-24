import { useEffect, useRef } from 'react';
import { FileText, X } from 'lucide-react';
import type { PlanDocument } from '@/api/types';
import { MarkdownDocument } from '@/components/workspace/MarkdownDocument';

interface PlanDocumentDialogProps {
  commitSha: string | null;
  document: PlanDocument;
  onClose: () => void;
}

export function PlanDocumentDialog({ commitSha, document, onClose }: PlanDocumentDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  function closeDialog() {
    dialogRef.current?.close();
  }

  return (
    <dialog
      ref={dialogRef}
      className="plan-document-dialog"
      aria-labelledby="plan-document-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) closeDialog();
      }}
    >
      <div className="flex h-full min-h-0 flex-col bg-card text-foreground">
        <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3.5">
          <div className="rounded-md bg-primary/10 p-2 text-primary">
            <FileText className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id="plan-document-title" className="truncate text-sm font-semibold">
              {document.name}
            </h2>
            {commitSha && (
              <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                Plan commit {commitSha}
              </p>
            )}
          </div>
          <button
            type="button"
            className="btn btn-ghost h-8 w-8 shrink-0 p-0"
            onClick={closeDialog}
            aria-label={`Close ${document.name}`}
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6 sm:px-8 lg:px-12">
          <div className="mx-auto max-w-4xl">
            {document.content ? (
              <MarkdownDocument content={document.content} />
            ) : (
              <p className="text-sm italic text-muted-foreground">Document content unavailable.</p>
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}
