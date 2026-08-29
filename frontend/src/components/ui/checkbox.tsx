"use client"

import * as React from "react"
import { Checkbox as BaseCheckbox } from "@base-ui/react/checkbox"
import { Check } from "lucide-react"
import { cn } from "@/lib/utils"

function Checkbox({
  className,
  checked,
  onCheckedChange,
  ...props
}: React.ComponentProps<typeof BaseCheckbox.Root> & {
  onCheckedChange?: (checked: boolean) => void;
}) {
  return (
    <BaseCheckbox.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      className={cn(
        "peer h-4 w-4 shrink-0 rounded-sm border border-primary ring-offset-background focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 flex items-center justify-center transition-colors data-checked:bg-primary data-checked:text-primary-foreground",
        className
      )}
      {...props}
    >
      <BaseCheckbox.Indicator>
        <Check className="h-3.5 w-3.5 text-white" />
      </BaseCheckbox.Indicator>
    </BaseCheckbox.Root>
  )
}
Checkbox.displayName = "Checkbox"

export { Checkbox }
