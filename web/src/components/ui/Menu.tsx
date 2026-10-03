import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "./cn";

export const MenuRoot = Dropdown.Root;
export const MenuTrigger = Dropdown.Trigger;
export const MenuSub = Dropdown.Sub;

const itemStyle =
  "flex h-8 cursor-pointer items-center gap-2 rounded-chip px-2 text-body outline-none " +
  "data-[highlighted]:bg-dispatch-wash data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50";

export function MenuContent({
  children,
  align = "start",
  className,
}: {
  children: ReactNode;
  align?: "start" | "end";
  className?: string;
}) {
  return (
    <Dropdown.Portal>
      <Dropdown.Content
        align={align}
        sideOffset={6}
        className={cn(
          "float-in z-50 min-w-48 rounded-panel border border-rule bg-sheet p-1 shadow-float",
          className,
        )}
      >
        {children}
      </Dropdown.Content>
    </Dropdown.Portal>
  );
}

export function MenuItem({
  children,
  onSelect,
  count,
}: {
  children: ReactNode;
  onSelect: () => void;
  count?: number | undefined;
}) {
  return (
    <Dropdown.Item className={itemStyle} onSelect={onSelect}>
      <span className="flex-1">{children}</span>
      {count !== undefined && <span className="tnum text-meta text-pencil">{count}</span>}
    </Dropdown.Item>
  );
}

/** Multi-select option with its facet count ("Blocked 14"). Stays open so several can be ticked. */
export function MenuCheckbox({
  checked,
  onCheckedChange,
  count,
  children,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  count?: number | undefined;
  children: ReactNode;
}) {
  return (
    <Dropdown.CheckboxItem
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(event) => {
        event.preventDefault();
      }}
      className={itemStyle}
    >
      <span className="flex size-4 items-center justify-center">
        <Dropdown.ItemIndicator>
          <Check className="size-4 text-dispatch" strokeWidth={1.75} />
        </Dropdown.ItemIndicator>
      </span>
      <span className="flex-1">{children}</span>
      {count !== undefined && <span className="tnum text-meta text-pencil">{count}</span>}
    </Dropdown.CheckboxItem>
  );
}

export function MenuRadioGroup({
  value,
  onValueChange,
  children,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <Dropdown.RadioGroup value={value} onValueChange={onValueChange}>
      {children}
    </Dropdown.RadioGroup>
  );
}

export function MenuRadio({ value, children }: { value: string; children: ReactNode }) {
  return (
    <Dropdown.RadioItem value={value} className={itemStyle}>
      <span className="flex size-4 items-center justify-center">
        <Dropdown.ItemIndicator>
          <Check className="size-4 text-dispatch" strokeWidth={1.75} />
        </Dropdown.ItemIndicator>
      </span>
      {children}
    </Dropdown.RadioItem>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return (
    <Dropdown.Label className="px-2 py-1 text-small font-strong text-pencil">
      {children}
    </Dropdown.Label>
  );
}

export function MenuSeparator() {
  return <Dropdown.Separator className="my-1 h-px bg-rule" />;
}

export const MenuSubTrigger = Dropdown.SubTrigger;
