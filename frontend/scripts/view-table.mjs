import "dotenv/config";
import { PrismaClient } from "../src/generated/prisma/client.ts";
import { PrismaPg } from "@prisma/adapter-pg";

const adapter = new PrismaPg({ connectionString: process.env.DATABASE_URL });
const prisma = new PrismaClient({ adapter });

const [, , tableName] = process.argv;
const tables = {
  organizations: () => prisma.organization.findMany(),
  users: () => prisma.user.findMany(),
  vendors: () => prisma.vendor.findMany(),
  events: () => prisma.event.findMany(),
  spaces: () => prisma.space.findMany(),
  applications: () => prisma.application.findMany(),
  bookings: () => prisma.booking.findMany(),
  payments: () => prisma.payment.findMany(),
  waitlist: () => prisma.waitlistEntry.findMany(),
  audit: () => prisma.auditEvent.findMany({ orderBy: { createdAt: "desc" }, take: 20 }),
};

if (!tableName || !tables[tableName]) {
  console.log("Usage: node scripts/view-table.mjs <table>");
  console.log("Tables:", Object.keys(tables).join(", "));
  process.exit(1);
}

const rows = await tables[tableName]();
console.table(rows);
console.log(`${rows.length} row(s)`);
await prisma.$disconnect();
