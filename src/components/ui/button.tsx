import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-semibold transition-[transform,background-color,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 active:translate-y-px",
  {
    variants: {
      variant: {
        default: "rounded-xl bg-primary px-5 py-3 text-primary-foreground shadow-button hover:bg-primary-hover",
        destructive: "rounded-md bg-destructive px-4 py-2 text-destructive-foreground hover:bg-destructive/90",
        quiet: "rounded-lg bg-secondary px-3 py-2 text-secondary-foreground hover:bg-accent",
        danger: "rounded-xl bg-destructive px-4 py-2.5 text-destructive-foreground hover:bg-destructive/90",
        outline: "rounded-md border border-input bg-background px-4 py-2 hover:bg-accent hover:text-accent-foreground",
        secondary: "rounded-md bg-secondary px-4 py-2 text-secondary-foreground hover:bg-secondary/80",
        ghost: "rounded-md px-4 py-2 hover:bg-accent hover:text-accent-foreground",
        link: "rounded-md px-4 py-2 text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-12 text-sm",
        sm: "h-10 text-sm",
        lg: "h-11 rounded-md px-8 text-sm",
        icon: "size-10 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Component = asChild ? Slot : "button";
    return <Component ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };