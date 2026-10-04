import { Terminal } from '@xterm/xterm';
import { SerializeAddon } from '@xterm/addon-serialize';
import { useEffect, useRef, useState } from 'react';

export type StageTerminalStatus = 'connecting' | 'connected' | 'finished' | 'error';

const TERMINAL_SCROLLBACK = 2_000;

function terminalUrl(projectId: string, triageId: string, stageRunId: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/projects/${encodeURIComponent(projectId)}/features/${encodeURIComponent(triageId)}/stages/${encodeURIComponent(stageRunId)}/terminal`;
}

function terminalStorageKey(projectId: string, triageId: string, stageRunId: string): string {
  return `agentplanex:stage-terminal:${projectId}:${triageId}:${stageRunId}`;
}

interface StoredTerminalState {
  serialized: string;
  sequence: number;
}

export function useStageTerminal(
  projectId: string | undefined,
  triageId: string | undefined,
  stageRunId: string | undefined,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<StageTerminalStatus>('connecting');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId || !triageId || !stageRunId || !containerRef.current) {
      setStatus('error');
      setError('The terminal URL is missing its Stage identity.');
      return;
    }

    const terminal = new Terminal({
      cols: 120,
      rows: 40,
      convertEol: false,
      cursorBlink: false,
      disableStdin: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      fontSize: 12,
      scrollback: TERMINAL_SCROLLBACK,
      theme: {
        background: '#07080b',
        foreground: '#d4d4d8',
        cursor: '#d4d4d8',
      },
    });
    const serializeAddon = new SerializeAddon();
    terminal.loadAddon(serializeAddon);
    const storageKey = terminalStorageKey(projectId, triageId, stageRunId);
    let lastReceivedSequence = 0;
    let lastParsedSequence = 0;
    try {
      const stored = window.sessionStorage.getItem(storageKey);
      if (stored) {
        const saved = JSON.parse(stored) as Partial<StoredTerminalState>;
        if (typeof saved.serialized === 'string' && typeof saved.sequence === 'number') {
          terminal.write(saved.serialized);
          lastReceivedSequence = saved.sequence;
          lastParsedSequence = saved.sequence;
        }
      }
    } catch {
      // Session storage may be unavailable or full; live output still works.
    }
    terminal.open(containerRef.current);

    let disposed = false;
    let retryTimer: number | undefined;
    let socket: WebSocket | null = null;

    const saveTerminalState = () => {
      try {
        window.sessionStorage.setItem(
          storageKey,
          JSON.stringify({
            serialized: serializeAddon.serialize({ scrollback: TERMINAL_SCROLLBACK }),
            sequence: lastParsedSequence,
          } satisfies StoredTerminalState),
        );
      } catch {
        // A full or unavailable session storage must not interrupt the terminal.
      }
    };

    const connect = () => {
      if (disposed) return;
      setStatus('connecting');
      socket = new WebSocket(terminalUrl(projectId, triageId, stageRunId));
      socket.onopen = () => {
        if (lastReceivedSequence === 0) terminal.reset();
        setStatus('connected');
        setError(null);
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as {
            type?: string;
            sequence?: number;
            data?: string;
          };
          if (message.type === 'output' && typeof message.data === 'string') {
            if (
              typeof message.sequence === 'number' &&
              message.sequence <= lastReceivedSequence
            ) {
              return;
            }
            if (typeof message.sequence === 'number') {
              lastReceivedSequence = message.sequence;
              terminal.write(message.data, () => {
                lastParsedSequence = Math.max(lastParsedSequence, message.sequence!);
              });
            } else {
              terminal.write(message.data);
            }
          } else if (message.type === 'terminal_end') {
            setStatus('finished');
            terminal.writeln('\r\n\x1b[90mStage execution finished.\x1b[0m');
          }
        } catch {
          terminal.writeln('\r\n\x1b[31mInvalid terminal event received.\x1b[0m');
        }
      };
      socket.onerror = () => {
        setError('The active Stage terminal could not be reached. Retrying…');
      };
      socket.onclose = (event) => {
        if (disposed) return;
        if (event.code === 1000) {
          setStatus('finished');
          return;
        }
        if (event.code === 4404 || event.code === 4409) {
          setStatus('error');
          setError(event.reason || 'The active Stage terminal is no longer available.');
          return;
        }
        retryTimer = window.setTimeout(connect, 500);
      };
    };

    window.addEventListener('pagehide', saveTerminalState);
    connect();

    return () => {
      disposed = true;
      window.removeEventListener('pagehide', saveTerminalState);
      saveTerminalState();
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socket?.close(1000, 'Terminal page closed');
      terminal.dispose();
    };
  }, [projectId, stageRunId, triageId]);

  return { containerRef, status, error };
}
