import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Baton's type scale and weights (globals.css @theme) are not Tailwind defaults.
// Without this, tailwind-merge reads `text-meta` as a colour and drops it next to
// `text-pencil`, and `font-strong` as a font family.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["figure", "title", "item", "section", "body", "meta", "small"] }],
      "font-weight": [{ font: ["body", "strong", "heading"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
