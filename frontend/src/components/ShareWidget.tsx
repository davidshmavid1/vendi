"use client";

import { useState, useRef, useEffect } from "react";

export function ShareWidget({ orgSlug, baseUrl }: { orgSlug: string; baseUrl: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"link" | "embed" | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const applyUrl = `${baseUrl}/o/${orgSlug}`;
  const embedCode = `<a href="${applyUrl}" target="_blank" rel="noopener" style="display:inline-block;padding:10px 20px;background:#18181b;color:#fff;border-radius:6px;text-decoration:none;font-family:sans-serif;font-size:14px;">Apply to be a Vendor</a>`;

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  async function copy(text: string, which: "link" | "embed") {
    await navigator.clipboard.writeText(text);
    setCopied(which);
    setTimeout(() => setCopied(null), 1500);
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
      >
        Share
      </button>

      {open && (
        <div className="fixed inset-x-4 top-20 z-10 rounded-lg border border-zinc-200 bg-white p-4 shadow-lg dark:border-zinc-800 dark:bg-zinc-900 sm:absolute sm:inset-x-auto sm:top-auto sm:right-0 sm:mt-2 sm:w-[30rem]">
          <p className="text-sm font-medium">Direct link</p>
          <p className="mt-1 text-xs text-zinc-500">Share with a vendor by text, DM, or email.</p>
          <div className="mt-2 flex gap-2">
            <input
              readOnly
              value={applyUrl}
              className="w-full rounded-md border border-zinc-300 bg-zinc-50 px-2 py-1.5 text-xs dark:border-zinc-700 dark:bg-zinc-950"
              onFocus={(e) => e.target.select()}
            />
            <button
              onClick={() => copy(applyUrl, "link")}
              className="shrink-0 rounded-md bg-zinc-900 px-2.5 py-1.5 text-xs font-medium text-white dark:bg-white dark:text-zinc-900"
            >
              {copied === "link" ? "Copied" : "Copy"}
            </button>
          </div>

          <p className="mt-4 text-sm font-medium">Embed on your website</p>
          <p className="mt-1 text-xs text-zinc-500">Paste this into your site&apos;s HTML.</p>
          <div className="mt-2 flex gap-2">
            <textarea
              readOnly
              value={embedCode}
              rows={6}
              className="w-full rounded-md border border-zinc-300 bg-zinc-50 px-2 py-1.5 text-xs dark:border-zinc-700 dark:bg-zinc-950"
              onFocus={(e) => e.target.select()}
            />
            <button
              onClick={() => copy(embedCode, "embed")}
              className="shrink-0 self-start rounded-md bg-zinc-900 px-2.5 py-1.5 text-xs font-medium text-white dark:bg-white dark:text-zinc-900"
            >
              {copied === "embed" ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
