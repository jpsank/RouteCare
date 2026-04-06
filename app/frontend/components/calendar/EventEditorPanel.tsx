import { useEffect, useMemo, useRef, useState } from "react";
import type { Message, Visit } from "../../types";
import { useSwipeDown } from "./hooks/useSwipeDown";
import { patientColor, statusOptions } from "./utils";

type Props = {
  editorMode: "none" | "add" | "edit";
  selectedVisit: Visit | null;
  visitStartInput: string;
  setVisitStartInput: (value: string) => void;
  visitStatusInput: Visit["status"];
  setVisitStatusInput: (value: Visit["status"]) => void;
  savingVisit: boolean;
  saveVisit: () => Promise<void>;
  closeEditor: () => void;
  position?: { x: number; y: number } | null;
  onSendMessage?: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<Message | undefined>;
  onEditPatient?: (patientId: number) => void;
};

export function EventEditorPanel({
  editorMode,
  selectedVisit,
  visitStartInput,
  setVisitStartInput,
  visitStatusInput,
  setVisitStatusInput,
  savingVisit,
  saveVisit,
  closeEditor,
  position,
  onSendMessage,
  onEditPatient,
}: Props) {
  const [sendingMessage, setSendingMessage] = useState(false);
  const [messageSent, setMessageSent] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const { handleRef, sheetStyle } = useSwipeDown(closeEditor);

  const isOpen = editorMode !== "none" || Boolean(selectedVisit);

  useEffect(() => {
    setMessageSent(false);
  }, [selectedVisit?.id, editorMode]);

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!panelRef.current) return;
      if (panelRef.current.contains(event.target as Node)) return;
      // Don't close if a higher-z modal (patient editor, settings) is open
      const target = event.target as HTMLElement;
      if (target.closest("[data-modal-overlay]")) return;
      closeEditor();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [isOpen, closeEditor]);

  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  const panelStyle = useMemo(() => {
    if (isMobile) return {};
    const x = Math.max(12, Math.min((position?.x ?? window.innerWidth / 2) + 8, window.innerWidth - 380));
    const y = Math.max(12, Math.min((position?.y ?? 120) + 8, window.innerHeight - 500));
    return { left: `${x}px`, top: `${y}px` };
  }, [position, isMobile]);

  if (!isOpen) return null;

  return (
    <div ref={panelRef} className={isMobile ? "fixed inset-x-0 bottom-0 z-[1000] p-3" : "rc-popover"} style={panelStyle} role="dialog" aria-modal="false">
      <div className={`${isMobile ? "rounded-t-2xl" : ""} rc-popover-card space-y-3`} style={isMobile ? sheetStyle : undefined}>
        {isMobile && <div ref={handleRef} className="flex h-8 w-full cursor-grab items-center justify-center"><div className="h-1 w-10 rounded-full bg-gray-300" /></div>}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {selectedVisit && (
              <span
                className="inline-block h-3 w-3 rounded-full flex-none"
                style={{ backgroundColor: patientColor(selectedVisit.patient_id).accent }}
              />
            )}
            <h3 className="text-sm font-semibold text-gray-900">
              {editorMode === "add" ? "Add Patient" : selectedVisit && onEditPatient ? (
                <button
                  className="border-0 bg-transparent p-0 text-sm font-semibold shadow-none hover:underline"
                  style={{ color: patientColor(selectedVisit.patient_id).accent }}
                  onClick={() => onEditPatient(selectedVisit.patient_id)}
                >
                  {selectedVisit.patient_name}
                </button>
              ) : (selectedVisit?.patient_name ?? "Edit Event")}
            </h3>
          </div>
          <button className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600" onClick={closeEditor} aria-label="Close">
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>

        {selectedVisit && (
          <>
            {(selectedVisit.drive_from_previous_minutes || 0) > 0 && (
              <div>
                <span className="rc-transit">
                  🚗 {selectedVisit.position_in_day === 0 ? "From home" : "Transit"}:{" "}
                  {selectedVisit.drive_from_previous_minutes || 0} min
                </span>
              </div>
            )}
            <div className="rc-field">
              <span className="rc-label">Visit Start</span>
              <input type="datetime-local" value={visitStartInput} onChange={(e) => setVisitStartInput(e.target.value)} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Visit Status</span>
              <select value={visitStatusInput} onChange={(e) => setVisitStatusInput(e.target.value as Visit["status"])}>
                {statusOptions().map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <button className="btn-primary w-full" onClick={saveVisit} disabled={savingVisit}>
              {savingVisit ? "Saving..." : "Save Visit"}
            </button>

            {onSendMessage && !messageSent && (
              <button
                className="btn-secondary w-full"
                disabled={sendingMessage}
                onClick={async () => {
                  setSendingMessage(true);
                  try {
                    await onSendMessage(selectedVisit.id, "sms", "", true);
                    setMessageSent(true);
                  } finally {
                    setSendingMessage(false);
                  }
                }}
              >
                {sendingMessage
                  ? "Sending..."
                  : selectedVisit.status === "confirmed"
                    ? "Send Reminder"
                    : "Send Confirmation"}
              </button>
            )}
            {messageSent && (
              <p className="text-center text-xs text-emerald-600 font-medium">Message sent</p>
            )}
          </>
        )}

        {/* Patient info (read-only, click name above to edit) */}
        {selectedVisit && (
          <div className="text-[11px] text-gray-400">
            {selectedVisit.patient_address || "No address on file"}
          </div>
        )}
      </div>
    </div>
  );
}
