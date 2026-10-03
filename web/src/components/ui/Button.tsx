import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, Ref } from "react";

import { cn } from "./cn";

type Variant = "primary" | "secondary" | "quiet" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-dispatch text-on-dispatch hover:brightness-110",
  secondary: "bg-sheet text-ink border border-rule hover:bg-dispatch-wash",
  quiet: "bg-transparent text-ink hover:bg-dispatch-wash",
  danger: "bg-p0 text-on-dispatch hover:brightness-110",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  /** Shows a spinner and ignores clicks. Width doesn't change, so nothing jumps. */
  pending?: boolean;
  ref?: Ref<HTMLButtonElement>;
}

export function Button({
  variant = "secondary",
  pending = false,
  className,
  children,
  disabled,
  type = "button",
  onClick,
  ref,
  ...rest
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      aria-busy={pending || undefined}
      disabled={disabled}
      onClick={(event) => {
        if (pending) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
      className={cn(
        "relative inline-flex h-9 items-center justify-center gap-2 rounded-chip px-3 text-body font-strong",
        "cursor-pointer transition-colors duration-100 ease-linear",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        className,
      )}
      {...rest}
    >
      <span className={cn("inline-flex items-center gap-2", pending && "invisible")}>
        {children}
      </span>
      {pending && (
        <span className="absolute inset-0 flex items-center justify-center" role="status">
          <Loader2 className="spin size-4" strokeWidth={1.75} aria-hidden />
          <span className="sr-only-live">Working</span>
        </span>
      )}
    </button>
  );
}
