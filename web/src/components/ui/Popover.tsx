import * as RadixPopover from "@radix-ui/react-popover";
import type { ReactNode } from "react";

import { cn } from "./cn";

export const PopoverRoot = RadixPopover.Root;
export const PopoverAnchor = RadixPopover.Anchor;
export const PopoverTrigger = RadixPopover.Trigger;
export const PopoverClose = RadixPopover.Close;

/** A small floating panel (DESIGN §2.4): 8 px radius, shadow, 160 ms entrance. Esc closes it. */
export function PopoverContent({
  children,
  className,
  label,
  align = "start",
  side = "bottom",
  onInteractOutside,
}: {
  children: ReactNode;
  className?: string;
  /** Accessible name of the panel. */
  label: string;
  align?: "start" | "center" | "end";
  side?: "top" | "right" | "bottom" | "left";
  /** Return true for targets that must not close it (the control that opened it). */
  onInteractOutside?: (target: HTMLElement) => boolean;
}) {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content
        aria-label={label}
        align={align}
        side={side}
        sideOffset={8}
        collisionPadding={12}
        onInteractOutside={(event) => {
          // A menu that just closed hands focus back to its trigger; that must not close this.
          if (event.target instanceof HTMLElement && onInteractOutside?.(event.target)) {
            event.preventDefault();
          }
        }}
        className={cn(
          "float-in z-50 w-[min(360px,calc(100vw-24px))] rounded-panel border border-rule bg-sheet p-4 shadow-float",
          className,
        )}
      >
        {children}
      </RadixPopover.Content>
    </RadixPopover.Portal>
  );
}
