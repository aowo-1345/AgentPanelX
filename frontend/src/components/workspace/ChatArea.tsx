import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleX,
  FileText,
  History,
  LockKeyhole,
  Loader2,
  Send,
  TerminalSquare,
  User,
} from 'lucide-react';
import { type KeyboardEvent, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AttributionData, ConversationMessage, FeatureAction, Panel } from '@/api/types';
import { ActionCard } from './ActionCard';
import { AttributionDialog } from './AttributionDialog';

interface CommandNotice {
  kind: 'success' | 'warning' | 'error';
  text: string;
  activationId?: string;
}

interface ChatAreaProps {
  conversation: Panel<ConversationMessage[]>;
  attribution?: Panel<AttributionData>;
  actions: FeatureAction[];
  activationStatus: string | null;
  activationHasReply: boolean;
  pendingAction: FeatureAction | null;
  sending: boolean;
  notice: CommandNotice | null;
  onSend: (content: string) => Promise<boolean>;
  onAction: (action: FeatureAction, feedback?: string) => Promise<void>;
  readOnly?: boolean;
  readOnlyLabel?: string;
}

function OwnerMessage({ content }: { content: string }) {
  return (
    <div className="chat-markdown">
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
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function MessageBubble({ message }: { message: ConversationMessage }) {
  if (message.role === 'tool' && message.tool_activity) {
    return <ToolActivityRow activity={message.tool_activity} />;
  }

  if (message.role === 'status') {
    return (
      <div className="flex justify-center">
        <span className="max-w-[85%] rounded-full bg-muted/60 px-3 py-1 text-center text-[11px] text-muted-foreground">
          {message.content}
        </span>
      </div>
    );
  }

  const user = message.role === 'user';
  return (
    <div className={`flex gap-2 ${user ? 'justify-end' : 'justify-start'}`}>
      {!user && (
        <div className="mt-auto flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15">
          <Bot className="h-3.5 w-3.5 text-emerald-400" />
        </div>
      )}
      <div className="max-w-[78%] space-y-1">
        <div className={`text-[10px] text-muted-foreground ${user ? 'text-right' : ''}`}>
          {user ? 'You' : 'Project Owner'}
        </div>
        <div
          className={`rounded-lg border px-3 py-2 text-sm leading-relaxed ${
            user
              ? 'whitespace-pre-wrap rounded-tr-sm border-primary/25 bg-primary/15'
              : 'rounded-tl-sm border-border bg-card'
          }`}
        >
          {user ? message.content : <OwnerMessage content={message.content} />}
        </div>
      </div>
      {user && (
        <div className="mt-auto flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
          <User className="h-3.5 w-3.5 text-primary" />
        </div>
      )}
    </div>
  );
}

function toolLabel(name: string) {
  return name
    .split('_')
    .filter(Boolean)
    .map((part) => `${part[0]?.toUpperCase() ?? ''}${part.slice(1)}`)
    .join(' ');
}

function ToolActivityRow({
  activity,
}: {
  activity: NonNullable<ConversationMessage['tool_activity']>;
}) {
  const running = activity.status === 'running';
  const failed = activity.status === 'failed';
  const tone = failed ? 'text-red-300' : running ? 'text-amber-300' : 'text-emerald-300';
  const label = activity.status[0].toUpperCase() + activity.status.slice(1);

  return (
    <div className="flex justify-start pl-8">
      <details className="group w-full max-w-[78%] rounded-lg border border-border bg-card/70 text-xs">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-muted-foreground marker:hidden">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
          <TerminalSquare className="h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate font-medium text-foreground">
            {toolLabel(activity.name)}
          </span>
          <span className={`flex shrink-0 items-center gap-1.5 ${tone}`}>
            {running ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : failed ? (
              <CircleX className="h-3.5 w-3.5" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            {label}
          </span>
        </summary>
        <div className="space-y-3 border-t border-border px-3 py-3">
          <ToolPreview label="Input" value={activity.input_preview} />
          {activity.output_preview !== null && (
            <ToolPreview label="Output" value={activity.output_preview} />
          )}
        </div>
      </details>
    </div>
  );
}

function ToolPreview({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-background/70 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {value}
      </pre>
    </div>
  );
}

export function ChatArea({
  conversation,
  attribution,
  actions,
  activationStatus,
  activationHasReply,
  pendingAction,
  sending,
  notice,
  onSend,
  onAction,
  readOnly = false,
  readOnlyLabel = 'This workspace snapshot is read-only.',
}: ChatAreaProps) {
  const [text, setText] = useState('');
  const [proposalsOpen, setProposalsOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const hasRunningTool = conversation.data?.some(
    (message) => message.role === 'tool' && message.tool_activity?.status === 'running',
  );
  const attributionState = attribution?.data?.state ?? 'idle';
  const proposalCount = attribution?.data?.reports.length ?? 0;

  useEffect(() => {
    if (readOnly) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activationStatus, conversation.data, notice, readOnly]);

  async function send() {
    const content = text.trim();
    if (!content || sending || pendingAction) return;
    if (await onSend(content)) {
      setText('');
    }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/15">
            <Bot className="h-4 w-4 text-emerald-400" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium">Project Owner</div>
            <div className="truncate text-[11px] text-muted-foreground">
              Codex{activationStatus ? ` · activation ${activationStatus.toLowerCase()}` : ' · local runtime'}
            </div>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-ghost h-8 shrink-0 px-2"
          aria-haspopup="dialog"
          onClick={() => setProposalsOpen(true)}
        >
          {attributionState === 'running' ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-300" />
          ) : attributionState === 'failed' ? (
            <CircleX className="h-3.5 w-3.5 text-red-300" />
          ) : proposalCount > 0 ? (
            <FileText className="h-3.5 w-3.5" />
          ) : (
            <History className="h-3.5 w-3.5" />
          )}
          {attributionState === 'running'
            ? '归因中'
            : attributionState === 'failed'
              ? '归因失败'
              : 'Proposals'}
          {proposalCount > 0 && <span className="text-[10px]">{proposalCount}</span>}
        </button>
      </header>

      {proposalsOpen && (
        <AttributionDialog panel={attribution} onClose={() => setProposalsOpen(false)} />
      )}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {conversation.error ? (
          <div className="flex items-start gap-2 rounded-md border border-red-500/20 bg-red-500/10 p-3 text-xs text-red-300">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>Conversation unavailable: {conversation.error}</span>
          </div>
        ) : (conversation.data?.length ?? 0) === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 text-center">
            <Bot className="h-8 w-8 text-muted-foreground/30" />
            <p className="text-sm text-muted-foreground">No conversation yet.</p>
            <p className="max-w-sm text-xs text-muted-foreground/60">
              Begin the feature when that action is available, then send Project Owner a message.
            </p>
          </div>
        ) : (
          conversation.data?.map((message) => (
            <MessageBubble key={message.message_id} message={message} />
          ))
        )}

        {activationStatus === 'RUNNING' && !hasRunningTool && !activationHasReply && (
          <div className="flex justify-center">
            <span className="flex max-w-[85%] items-center gap-2 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1.5 text-[11px] text-emerald-300">
              <Loader2 className="h-3 w-3 animate-spin" />
              Project Owner Thinking…
            </span>
          </div>
        )}

        <ActionCard actions={actions} pendingAction={pendingAction} onAction={onAction} />

        {notice && (
          <div
            className={`rounded-md border p-2.5 text-xs ${
              notice.kind === 'success'
                ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
                : notice.kind === 'warning'
                  ? 'border-amber-500/20 bg-amber-500/10 text-amber-300'
                  : 'border-red-500/20 bg-red-500/10 text-red-300'
            }`}
          >
            {notice.text}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-border p-4">
        {readOnly ? (
          <div className="flex items-center gap-2 rounded-lg border border-primary/15 bg-primary/5 px-3 py-2.5 text-[11px] text-muted-foreground">
            <LockKeyhole className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span>{readOnlyLabel}</span>
          </div>
        ) : (
          <>
            <div className="flex items-end gap-2">
              <textarea
                className="field min-h-[68px] max-h-36 flex-1 resize-y py-2"
                value={text}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={keyDown}
                placeholder="Message Project Owner…"
                disabled={sending || pendingAction !== null}
              />
              <button
                className="btn btn-primary h-9 w-9 p-0"
                onClick={() => void send()}
                disabled={!text.trim() || sending || pendingAction !== null}
                aria-label="Send message"
              >
                {sending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </button>
            </div>
            <p className="mt-1.5 text-[10px] text-muted-foreground/50">
              Enter to send · Shift+Enter for a new line. Refresh to retrieve later Owner updates.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

export type { CommandNotice };
