function Bar({ className }: { className: string }) {
  return <div className={`animate-pulse rounded bg-muted ${className}`} />;
}

export function SkeletonCard() {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-3">
      <Bar className="h-2.5 w-16" />
      <Bar className="h-4 w-4/5" />
      <Bar className="h-3 w-3/5" />
      <Bar className="h-5 w-20" />
    </div>
  );
}

export function SkeletonWorkspace() {
  return (
    <div className="flex h-full gap-4 p-4">
      <div className="flex flex-1 flex-col gap-4">
        <Bar className="h-12 w-2/3" />
        <Bar className="ml-auto h-16 w-1/2" />
        <Bar className="h-20 w-3/4" />
        <Bar className="mt-auto h-20 w-full" />
      </div>
      <div className="hidden w-80 space-y-3 lg:block">
        {Array.from({ length: 5 }, (_, index) => (
          <Bar key={index} className="h-24 w-full" />
        ))}
      </div>
    </div>
  );
}
