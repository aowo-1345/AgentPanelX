import { AlertCircle, Bot, Loader2, Send, User } from 'lucide-react';
import { type KeyboardEvent, useEffect, useRef, useState } from 'react';
import type {
  ConversationMessage,
  FeatureAction,
  Panel,
} from '@/api/types';
import { ActionCard } from './ActionCard';

interface CommandNotice {
  kind: 'success' | 'warning' | 'error';
  text: string;
}

interface ChatAreaProps {
  conversation: Panel<ConversationMessage[]>;
  actions: FeatureAction[];
  activationStatus: string | null;
  pendingAction: FeatureAction | null;
  sending: boolean;
  notice: CommandNotice | null;
  onSend: (content: string) => Promise<boolean>;
  onAction: (action: FeatureAction, feedback?: string) => Promise<void>;
}

function MessageBubble({ message }: { message: ConversationMessage }) {
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
          className={`whitespace-pre-wrap rounded-lg border px-3 py-2 text-sm leading-relaxed ${
            user
              ? 'rounded-tr-sm border-primary/25 bg-primary/15'
              : 'rounded-tl-sm border-border bg-card'
          }`}
        >
          {message.content}
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

export function ChatArea({
  conversation,
  actions,
  activationStatus,
  pendingAction,
  sending,
  notice,
  onSend,
  onAction,
}: ChatAreaProps) {
  const [text, setText] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversation.data, notice]);

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
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500/15">
          <Bot className="h-4 w-4 text-emerald-400" />
        </div>
        <div>
          <div className="text-sm font-medium">Project Owner</div>
          <div className="text-[11px] text-muted-foreground">
            Codex{activationStatus ? ` · activation ${activationStatus.toLowerCase()}` : ' · local runtime'}
          </div>
        </div>
      </header>

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
      </div>
    </div>
  );
}

export type { CommandNotice };
