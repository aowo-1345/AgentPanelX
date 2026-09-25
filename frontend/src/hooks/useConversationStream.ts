import { useEffect, useRef, useState } from 'react';
import { api } from '@/api/client';
import type {
  ConversationMessage,
  ConversationPatchEvent,
  ConversationSnapshotEvent,
} from '@/api/types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isToolActivity(value: unknown): value is ConversationMessage['tool_activity'] {
  if (value === null) return true;
  if (!isRecord(value)) return false;
  const activity = value;
  return (
    typeof activity.name === 'string' &&
    (activity.status === 'running' ||
      activity.status === 'completed' ||
      activity.status === 'failed') &&
    typeof activity.input_preview === 'string' &&
    (activity.output_preview === null || typeof activity.output_preview === 'string')
  );
}

function isConversationMessage(value: unknown): value is ConversationMessage {
  if (!isRecord(value)) return false;
  const message = value;
  return (
    typeof message.message_id === 'string' &&
    (message.role === 'user' ||
      message.role === 'assistant' ||
      message.role === 'status' ||
      message.role === 'tool') &&
    typeof message.content === 'string' &&
    isToolActivity(message.tool_activity)
  );
}

function parseEvent(data: string): ConversationSnapshotEvent | ConversationPatchEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (!isRecord(value)) return null;
    const snapshot = value;
    if (
      typeof snapshot.cursor !== 'number' ||
      typeof snapshot.activation_has_reply !== 'boolean' ||
      !Array.isArray(snapshot.messages) ||
      !snapshot.messages.every(isConversationMessage)
    ) {
      return null;
    }
    return {
      cursor: snapshot.cursor,
      messages: snapshot.messages,
      activation_has_reply: snapshot.activation_has_reply,
    };
  } catch {
    return null;
  }
}

interface UseConversationStreamOptions {
  projectId?: string;
  triageId?: string;
  enabled: boolean;
}

export function useConversationStream({
  projectId,
  triageId,
  enabled,
}: UseConversationStreamOptions): ConversationSnapshotEvent | null {
  const [messages, setMessages] = useState<ConversationSnapshotEvent | null>(null);
  const cursor = useRef(0);

  useEffect(() => {
    if (!enabled || !projectId || !triageId) {
      return;
    }

    setMessages(null);
    cursor.current = 0;
    const source = new EventSource(api.conversationStreamUrl(projectId, triageId));
    source.addEventListener('snapshot', (event) => {
      if (!(event instanceof MessageEvent) || typeof event.data !== 'string') return;
      const snapshot = parseEvent(event.data);
      if (snapshot !== null) {
        cursor.current = snapshot.cursor;
        setMessages(snapshot);
      }
    });
    source.addEventListener('patch', (event) => {
      if (!(event instanceof MessageEvent) || typeof event.data !== 'string') return;
      const patch = parseEvent(event.data);
      if (patch === null) return;
      if (patch.cursor < cursor.current) return;
      cursor.current = patch.cursor;
      setMessages((current) => {
        const next = [...(current?.messages ?? [])];
        for (const message of patch.messages) {
          const index = next.findIndex((item) => item.message_id === message.message_id);
          if (index < 0) next.push(message);
          else next[index] = message;
        }
        return { ...patch, messages: next };
      });
    });

    return () => source.close();
  }, [enabled, projectId, triageId]);

  return messages;
}
