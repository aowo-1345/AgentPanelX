export function BrandMark({ className = 'h-8 w-8' }: { className?: string }) {
  return (
    <img
      className={`${className} block shrink-0 rounded-[10px] shadow-[0_10px_30px_rgba(37,99,235,0.16)]`}
      src={`${import.meta.env.BASE_URL}agentpanelx-mark.svg`}
      alt=""
      aria-hidden="true"
    />
  );
}
