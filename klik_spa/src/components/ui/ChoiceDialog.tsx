import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  // @ts-expect-error ignore
} from "@/components/ui/alert-dialog"

export interface Choice {
  label: string;
  onChoose: () => void;
  /** Shown instead of the button's action, greyed out, when the choice isn't available. */
  disabledReason?: string | null;
  primary?: boolean;
}

/** A question with more than one way forward, plus Cancel. */
export function ChoiceDialog({ isOpen, onClose, title, message, choices }: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  message: string;
  choices: Choice[];
}) {
  const reasons = choices.map((c) => c.disabledReason).filter(Boolean);
  return (
    <AlertDialog open={isOpen} onOpenChange={(open: boolean) => { if (!open) onClose(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{message}</AlertDialogDescription>
        </AlertDialogHeader>
        {reasons.length ? <p className="text-sm text-amber-600 dark:text-amber-400">{reasons.join(" ")}</p> : null}
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>Cancel</AlertDialogCancel>
          {choices.map((c) => (
            <button
              key={c.label}
              type="button"
              disabled={!!c.disabledReason}
              onClick={c.onChoose}
              className={`inline-flex h-10 items-center justify-center rounded-md px-4 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${
                c.primary ? "bg-beveren-600 text-white hover:bg-beveren-700" : "border border-gray-300 dark:border-gray-600 text-gray-900 dark:text-gray-100 hover:bg-gray-50 dark:hover:bg-gray-700"
              }`}
            >
              {c.label}
            </button>
          ))}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
