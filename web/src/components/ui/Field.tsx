import type { InputHTMLAttributes, ReactNode, Ref, TextareaHTMLAttributes } from "react";

import { cn } from "./cn";

const control =
  "w-full rounded-chip border border-rule bg-sheet px-3 text-body text-ink placeholder:text-pencil " +
  "aria-[invalid=true]:border-p0";

export function Input({
  className,
  ref,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement> }) {
  return <input ref={ref} className={cn(control, "h-9", className)} {...rest} />;
}

export function Textarea({
  className,
  ref,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { ref?: Ref<HTMLTextAreaElement> }) {
  return <textarea ref={ref} className={cn(control, "min-h-24 py-2", className)} {...rest} />;
}

/** Label above a control, with an optional hint and an inline error (`role="alert"`). */
export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  error?: string | undefined;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-meta font-strong text-ink">
        {label}
      </label>
      {children}
      {hint && !error && <p className="text-meta text-pencil">{hint}</p>}
      {error && (
        <p role="alert" className="text-meta text-p0">
          {error}
        </p>
      )}
    </div>
  );
}
