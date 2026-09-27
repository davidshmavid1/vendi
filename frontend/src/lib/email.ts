/**
 * Minimal email abstraction. Phase 1 just logs — swap `send` for a real
 * provider (Resend/Postmark/SES) in Phase 2 without touching call sites,
 * since every caller only depends on this interface.
 */
export type EmailMessage = {
  to: string;
  subject: string;
  body: string;
};

export interface EmailService {
  send(message: EmailMessage): Promise<void>;
}

class ConsoleEmailService implements EmailService {
  async send(message: EmailMessage): Promise<void> {
    console.log(`[email] to=${message.to} subject="${message.subject}"\n${message.body}`);
  }
}

export const emailService: EmailService = new ConsoleEmailService();
