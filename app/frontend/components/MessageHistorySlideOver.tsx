import { useEffect, useRef } from "react";
import type { Message } from "../types";

type Props = {
  messages: Message[];
  isOpen: boolean;
  onClose: () => void;
  onApprove: (messageId: number) => Promise<Message | undefined>;
};

function titleCase(value: string): string {
  return value.replaceAll("_", " ").split(" ").map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ");
}

function badgeClass(status: string): string {
  if (status === "confirmed" || status === "sent" || status === "received" || status === "completed") return "rc-badge rc-badge-success";
  if (status === "declined" || status === "failed") return "rc-badge rc-badge-danger";
  if (status === "pending_approval") return "rc-badge rc-badge-warning";
  return "rc-badge rc-badge-neutral";
}

export function MessageHistorySlideOver({ messages, isOpen, onClose, onApprove }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const pendingMessages = messages.filter((m) => m.status === "pending_approval" || m.status === "draft");
  const recentMessages = messages.filter((m) => m.status !== "pending_approval" && m.status !== "draft").slice(0, 30);

  return (
    <div className="fixed inset-0 z-[900] flex justify-end bg-black/20">
      <div ref={ref} className="flex h-full w-full max-w-sm flex-col bg-white shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-900">Messages</h3>
          <button
            className="rounded-md border-0 bg-transparent p-1 text-gray-400 shadow-none hover:text-gray-600"
            onClick={onClose}
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {/* Pending approvals */}
          {pendingMessages.length > 0 && (
            <div className="mb-4">
              <h4 className="mb-2 text-[10px] font-bold uppercase tracking-wider text-amber-600">
                Needs Approval ({pendingMessages.length})
              </h4>
              <div className="space-y-2">
                {pendingMessages.map((msg) => (
                  <div key={msg.id} className="rounded-lg border border-amber-200 bg-amber-50 p-2.5">
                    <div className="flex items-center gap-2 text-xs text-gray-500">
                      {msg.patient_name && <span className="font-semibold text-gray-800">{msg.patient_name}</span>}
                      <span className="rounded bg-gray-100 px-1 py-0.5 text-[10px] font-medium">{msg.channel === "sms" ? "SMS" : "Email"}</span>
                    </div>
                    {msg.body && <p className="mt-1 text-xs text-gray-700">{msg.body}</p>}
                    <button
                      className="btn-primary btn-xs mt-2"
                      onClick={() => void onApprove(msg.id)}
                    >
                      Approve & Send
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent messages */}
          <h4 className="mb-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">Recent</h4>
          {recentMessages.length === 0 && (
            <p className="py-4 text-center text-xs text-gray-400">No messages yet</p>
          )}
          <div className="space-y-1.5">
            {recentMessages.map((msg) => {
              const isInbound = msg.direction === "inbound";
              return (
                <div key={msg.id} className={`rounded-lg border border-gray-100 p-2.5 ${isInbound ? "border-l-[3px] border-l-indigo-400" : ""}`}>
                  <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
                    {msg.patient_name && <span className="font-medium text-gray-800">{msg.patient_name}</span>}
                    <span className="rounded bg-gray-100 px-1 py-0.5 text-[10px] font-medium">{msg.channel === "sms" ? "SMS" : "Email"}</span>
                    <span>{isInbound ? "In" : "Out"}</span>
                    <span className={badgeClass(msg.status)}>{titleCase(msg.status)}</span>
                  </div>
                  {msg.body && <p className="mt-1 text-[11px] leading-relaxed text-gray-600 line-clamp-2">{msg.body}</p>}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
