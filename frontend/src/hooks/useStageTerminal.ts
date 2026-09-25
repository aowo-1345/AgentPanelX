import { Terminal } from '@xterm/xterm';
import { useEffect, useRef, useState } from 'react';

export type StageTerminalStatus = 'connecting' | 'connected' | 'finished' | 'error';

function terminalUrl(projectId: string, triageId: string, stageRunId: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/api/projects/${encodeURIComponent(projectId)}/features/${encodeURIComponent(triageId)}/stages/${encodeURIComponent(stageRunId)}/terminal`;
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
      convertEol: true,
      cursorBlink: false,
      disableStdin: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      fontSize: 12,
      scrollback: 2_000,
      theme: {
        background: '#07080b',
        foreground: '#d4d4d8',
        cursor: '#d4d4d8',
      },
    });
    terminal.open(containerRef.current);
    terminal.writeln('\x1b[90mConnecting to the active Stage…\x1b[0m');

    const socket = new WebSocket(terminalUrl(projectId, triageId, stageRunId));
    socket.onopen = () => {
      setStatus('connected');
      setError(null);
    };
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as {
          type?: string;
          data?: string;
        };
        if (message.type === 'output' && typeof message.data === 'string') {
          terminal.write(message.data);
        } else if (message.type === 'terminal_end') {
          setStatus('finished');
          terminal.writeln('\r\n\x1b[90mStage execution finished.\x1b[0m');
        }
      } catch {
        terminal.writeln('\r\n\x1b[31mInvalid terminal event received.\x1b[0m');
      }
    };
    socket.onerror = () => {
      setStatus('error');
      setError('The active Stage terminal could not be reached.');
    };
    socket.onclose = (event) => {
      if (event.code !== 1000) {
        setStatus('error');
        setError(event.reason || 'The active Stage terminal closed unexpectedly.');
      }
    };

    return () => {
      socket.close(1000, 'Terminal page closed');
      terminal.dispose();
    };
  }, [projectId, stageRunId, triageId]);

  return { containerRef, status, error };
}
