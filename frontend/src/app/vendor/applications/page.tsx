import type { Metadata } from "next";
import { VendorApplications } from "./VendorApplications";

export const metadata: Metadata = { title: "My applications · Vendi" };

export default function VendorApplicationsPage() {
  return <VendorApplications />;
}
