import { Link } from "react-router-dom";

type Props = {
  invoice: { is_return?: number | boolean; return_against?: string | null; returns?: { name: string }[] | null };
};

const link = "font-medium text-beveren-700 dark:text-beveren-300 underline hover:no-underline";

/** A return links to the sale it reverses; a sale links to the credit notes against it. */
export function ReturnLinks({ invoice }: Props) {
  if (invoice.is_return && invoice.return_against) {
    return (
      <p className="text-sm text-gray-600 dark:text-gray-400">
        Return of <Link className={link} to={`/invoice/${invoice.return_against}`}>{invoice.return_against}</Link>
      </p>
    );
  }
  const returns = invoice.returns ?? [];
  if (!returns.length) return null;
  return (
    <p className="text-sm text-gray-600 dark:text-gray-400">
      Returned by{" "}
      {returns.map((r, i) => (
        <span key={r.name}>
          {i > 0 && ", "}
          <Link className={link} to={`/invoice/${r.name}`}>{r.name}</Link>
        </span>
      ))}
    </p>
  );
}
