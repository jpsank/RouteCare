import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

type ImportResult = {
  imported: number;
  errors: string[];
  header_map?: Record<string, string>;
};

type Props = {
  onImport: (file: File) => Promise<ImportResult | undefined>;
  disabled?: boolean;
};

const MAX_BYTES = 1_000_000;

const schema = z.object({
  file: z
    .instanceof(FileList)
    .refine((list) => list.length === 1, "Choose a CSV file")
    .refine((list) => list[0]?.size <= MAX_BYTES, "File must be under 1 MB")
    .refine(
      (list) => list[0]?.name.toLowerCase().endsWith(".csv"),
      "File must be a .csv",
    ),
});
type FormData = z.infer<typeof schema>;

export function PatientImportControl({ onImport, disabled }: Props) {
  const [result, setResult] = useState<ImportResult | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({ resolver: zodResolver(schema) });

  const onSubmit = handleSubmit(async (data) => {
    setResult(null);
    const file = data.file.item(0);
    if (!file) return;
    const response = await onImport(file);
    if (response) setResult(response);
    reset();
  });

  return (
    <form onSubmit={onSubmit} className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="file"
          accept=".csv,text/csv"
          className="cursor-pointer text-[11px] file:mr-2 file:cursor-pointer file:rounded file:border-0 file:bg-indigo-50 file:px-2 file:py-1 file:text-[11px] file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
          {...register("file")}
          disabled={disabled || isSubmitting}
        />
        <button
          type="submit"
          className="btn-ghost btn-sm"
          disabled={disabled || isSubmitting}
        >
          {isSubmitting ? "Importing..." : "Import CSV"}
        </button>
      </div>
      {errors.file?.message && (
        <p className="text-[11px] text-red-600">{errors.file.message}</p>
      )}
      {result && (
        <div className="text-[11px] space-y-1">
          <p className="text-emerald-700">Imported {result.imported} patient(s).</p>
          {result.header_map && Object.keys(result.header_map).length > 0 && (
            <details>
              <summary className="cursor-pointer text-gray-500">
                Column mapping ({Object.keys(result.header_map).length} matched)
              </summary>
              <ul className="mt-1 max-h-32 list-disc overflow-y-auto pl-4 text-gray-500">
                {Object.entries(result.header_map).map(([original, canonical]) => (
                  <li key={original}>
                    <span className="font-medium text-gray-700">{original}</span> → {canonical}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {result.errors.length > 0 && (
            <details>
              <summary className="cursor-pointer text-amber-700">
                {result.errors.length} row error(s)
              </summary>
              <ul className="mt-1 max-h-32 list-disc overflow-y-auto pl-4 text-gray-500">
                {result.errors.map((err, idx) => (
                  <li key={idx}>{err}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      <p className="text-[10px] text-gray-400">
        Column names are matched automatically — "Patient Name", "Phone #", "Zip",
        "Street", etc. all work. Required: name, phone, address, city, state, postal code.
      </p>
    </form>
  );
}
