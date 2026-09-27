import type { Metadata } from "next";
import { OrganizerHome } from "./OrganizerHome";

export const metadata: Metadata = { title: "Organizer review · Vendi" };

export default function OrganizerPage() {
  return <OrganizerHome />;
}
