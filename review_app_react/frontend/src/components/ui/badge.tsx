import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center justify-center rounded-sm border px-1.5 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 gap-1 [&_svg]:size-3",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary:
          "border-transparent bg-[var(--status-neutral-bg)] text-[var(--status-neutral-fg)]",
        destructive:
          "border-transparent bg-[var(--status-blocked-bg)] text-[var(--status-blocked-fg)]",
        outline: "border-border text-foreground bg-transparent",
        success:
          "border-transparent bg-[var(--status-eligible-bg)] text-[var(--status-eligible-fg)]",
        warning:
          "border-transparent bg-[var(--status-advisory-bg)] text-[var(--status-advisory-fg)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "span";
  return (
    <Comp
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
