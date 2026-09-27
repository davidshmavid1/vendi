import { test } from "node:test";
import assert from "node:assert/strict";
import { answerErrors, answersPayload, answerText, checkAnswers, loginHref, safeNext, type Question } from "./logic.ts";

const QUESTIONS: Question[] = [
  { id: "products", type: "short_text", label: "What?", required: true, choices: [] },
  { id: "about", type: "long_text", label: "About", required: false, choices: [] },
  { id: "power", type: "single_choice", label: "Power?", required: true, choices: ["Yes", "No"] },
  { id: "rules", type: "acknowledgement", label: "Rules", required: true, choices: [] },
];

test("safeNext only allows same-site relative paths", () => {
  assert.equal(safeNext("/vendor/applications"), "/vendor/applications");
  assert.equal(safeNext("https://evil.example"), "/markets");
  assert.equal(safeNext("//evil.example"), "/markets");
  assert.equal(safeNext("/\\evil.example"), "/markets");
  assert.equal(safeNext(null, "/x"), "/x");
  assert.equal(loginHref("/markets/1?a=b"), "/account/login?next=%2Fmarkets%2F1%3Fa%3Db");
});

test("checkAnswers mirrors the server rules", () => {
  assert.deepEqual(checkAnswers(QUESTIONS, {}), {
    products: "This question is required.",
    power: "This question is required.",
    rules: "Please check this box.",
  });
  assert.deepEqual(checkAnswers(QUESTIONS, { products: "Honey", power: "No", rules: true }), {});
  assert.deepEqual(checkAnswers(QUESTIONS, { products: "x".repeat(201), power: "Maybe", rules: true }), {
    products: "Must be at most 200 characters.",
    power: "Choose one of the listed options.",
  });
});

test("answersPayload trims text and omits blanks and unchecked boxes", () => {
  assert.deepEqual(answersPayload(QUESTIONS, { products: "  Honey ", about: "  ", power: "No", rules: false }), {
    products: "Honey",
    power: "No",
  });
});

test("answerErrors reads server field errors", () => {
  assert.deepEqual(answerErrors([{ question_id: "power", message: "Choose one." }, { other: 1 }]), { power: "Choose one." });
  assert.deepEqual(answerErrors(null), {});
});

test("answerText", () => {
  assert.equal(answerText(QUESTIONS[3], true), "Yes (checked)");
  assert.equal(answerText(QUESTIONS[3], undefined), "Not checked");
  assert.equal(answerText(QUESTIONS[1], undefined), "No answer");
});
