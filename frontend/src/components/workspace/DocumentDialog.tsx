import { type ReactNode, useEffect, useRef } from 'react';
import { X } from 'lucide-react';

interface DocumentDialogProps {
  titleId: string;
  title: string;
  subtitle?: ReactNode;
  icon: ReactNode;
  closeLabel: string;
  bodyClassName?: string;
  children: ReactNode;
  onClose: () => void;
}

export function DocumentDialog({
  titleId,
  title,
  subtitle,
  icon,
  closeLabel,
  bodyClassName = 'px-5 py-6 sm:px-8 lg:px-12',
  children,
  onClose,
}: DocumentDialogProps) {
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
      className="document-dialog"
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) closeDialog();
      }}
    >
      <div className="flex h-full min-h-0 flex-col bg-card text-foreground">
        <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3.5">
          <div className="rounded-md bg-primary/10 p-2 text-primary">{icon}</div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="truncate text-sm font-semibold">
              {title}
            </h2>
            {subtitle && (
              <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                {subtitle}
              </div>
            )}
          </div>
          <button
            type="button"
            className="btn btn-ghost h-8 w-8 shrink-0 p-0"
            onClick={closeDialog}
            aria-label={closeLabel}
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className={`min-h-0 flex-1 overflow-y-auto ${bodyClassName}`}>{children}</div>
      </div>
    </dialog>
  );
}
