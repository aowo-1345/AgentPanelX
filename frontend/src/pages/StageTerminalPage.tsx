import { ArrowLeft } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { StageTerminal } from '@/components/workspace/StageTerminal';

export function StageTerminalPage() {
  const navigate = useNavigate();
  const { projectId, triageId, stageRunId } = useParams<{
    projectId: string;
    triageId: string;
    stageRunId: string;
  }>();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex h-12 shrink-0 items-center border-b border-border px-4">
        <button
          className="btn btn-ghost h-8"
          onClick={() => navigate(`/projects/${projectId}/features/${triageId}`)}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Workspace
        </button>
      </header>
      <StageTerminal projectId={projectId} triageId={triageId} stageRunId={stageRunId} />
    </div>
  );
}
