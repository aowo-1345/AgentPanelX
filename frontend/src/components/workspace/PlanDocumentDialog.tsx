import { FileText } from 'lucide-react';
import type { PlanDocument } from '@/api/types';
import { DocumentDialog } from '@/components/workspace/DocumentDialog';
import { MarkdownDocument } from '@/components/workspace/MarkdownDocument';

interface PlanDocumentDialogProps {
  commitSha: string | null;
  document: PlanDocument;
  onClose: () => void;
}

export function PlanDocumentDialog({ commitSha, document, onClose }: PlanDocumentDialogProps) {
  return (
    <DocumentDialog
      titleId="plan-document-title"
      title={document.name}
      subtitle={commitSha ? <span className="font-mono">Plan commit {commitSha}</span> : undefined}
      icon={<FileText className="h-4 w-4" />}
      closeLabel={`Close ${document.name}`}
      onClose={onClose}
    >
      <div className="mx-auto max-w-4xl">
        {document.content ? (
          <MarkdownDocument content={document.content} />
        ) : (
          <p className="text-sm italic text-muted-foreground">Document content unavailable.</p>
        )}
      </div>
    </DocumentDialog>
  );
}
