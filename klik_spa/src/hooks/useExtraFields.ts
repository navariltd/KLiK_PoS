import { useEffect, useState } from "react";
import { usePOSProfileStore } from "../stores/posProfileStore";

export interface ResolvedExtraField {
  fieldname: string;
  label: string;
  fieldtype: string;
  options: string[];
  reqd: boolean;
}

interface Candidate { fieldname: string; label: string; fieldtype: string; options: string }

let _cache: Candidate[] | null = null;

export function useExtraFields(): { fields: ResolvedExtraField[] } {
  const posDetails = usePOSProfileStore((s) => s.posDetails);
  const [candidates, setCandidates] = useState<Candidate[]>(_cache || []);

  useEffect(() => {
    if (_cache) return;
    fetch("/api/method/klik_pos.api.pos_profile.get_pos_extra_field_candidates", {
      credentials: "include",
    })
      .then((r) => r.json())
      .then((d) => { _cache = d?.message || []; setCandidates(_cache); })
      .catch(() => setCandidates([]));
  }, []);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rows: any[] = (posDetails as any)?.custom_pos_extra_fields || [];
  const byName = new Map(candidates.map((c) => [c.fieldname, c]));

  const fields: ResolvedExtraField[] = rows
    .map((row) => {
      const c = byName.get(row.so_si_commonfield);
      if (!c) return null;
      return {
        fieldname: c.fieldname,
        label: c.label,
        fieldtype: c.fieldtype,
        options: c.fieldtype === "Select" ? (c.options || "").split("\n").filter(Boolean) : [],
        reqd: !!row.reqd,
      } as ResolvedExtraField;
    })
    .filter((f): f is ResolvedExtraField => f !== null);

  return { fields };
}
