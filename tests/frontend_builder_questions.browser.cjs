// Exercise the real shipped form, keyboard flow and answered layout with a synthetic bridge.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const frontend = path.resolve(__dirname, '../src-tauri/frontend');

for (const width of [1280, 640]) for (const paged of [false, true]) {
  test(`Builder ${paged ? 'paged form' : 'answer'} uses its widget with keyboard input at ${width}px`, async () => {
    const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'],
      channel: process.env.SYLVIA_TEST_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) });
    try {
      const page = await browser.newPage({ viewport: { width, height: 850 } });
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('https://sylvia-question.test/**', async route => {
        const name = new URL(route.request().url()).pathname.slice(1) || 'app.html';
        const file = path.resolve(frontend, name);
        if (!file.startsWith(frontend + path.sep) || !fs.existsSync(file)) return route.abort();
        let body = fs.readFileSync(file);
        if (name === 'app.js') body = body.toString().replace(
          "document.addEventListener('DOMContentLoaded', () => { void SylviaGMFrontend.init(); });", '')
          .replace('  // ---- Public API ----',
            'window.questionQA = { state, renderBuilderHistory, renderBuilderSession, ensureSessionActivity };\n  // ---- Public API ----');
        await route.fulfill({ body, contentType: name.endsWith('.js') ? 'text/javascript' :
          name.endsWith('.css') ? 'text/css' : name.endsWith('.png') ? 'image/png' : 'text/html' });
      });
      await page.goto('https://sylvia-question.test/');
      await page.evaluate((paged) => {
        const { state, renderBuilderHistory, ensureSessionActivity } = window.questionQA;
        document.getElementById('connecting-overlay').style.display = 'none';
        document.getElementById('session-frame').style.display = 'flex';
        state.view = 'session'; state.activeSessionId = 'question-chat'; state.activeSessionRole = 'builder';
        state.activeCampaignId = null;
        const chat = { display_name: 'Question fixture', session: {
          session_id: 'question-chat', role: 'builder', status: 'active', provider_id: 'test', model_id: 'test',
        } };
        state.builderChats = [chat];
        const question = { message_id: 'question', sequence: 1, kind: 'evidence', content: {
          kind: 'builder_tool_result', tool_name: 'ask_question', tool_call_id: 'question-call',
          result: { success: true, status: 'awaiting_answer', widget: { type: 'text', data: {
            id: 'concept', question: 'Who would you like to play?',
          } } },
        } };
        const background = { message_id: 'background', sequence: 103, kind: 'evidence', content: {
          kind: 'builder_tool_result', tool_name: 'ask_question', tool_call_id: 'background-call',
          result: { success: true, status: 'awaiting_answer', widget: { type: 'text', data: {
            id: 'background', question: 'What is their background?',
          } } },
        } };
        const questions = paged ? [question, background] : [question];
        const messages = paged ? [question, ...Array.from({ length: 101 }, (_, index) => ({
          sequence: index + 2, message_id: `progress-${index}`, kind: 'talk',
          content: { text: 'Builder progress' },
        })), background] : [question];
        const historyPage = () => ({ messages: messages.slice(-50), latest_sequence: messages.at(-1).sequence,
          latest_user_sequence: window.questionSubmissions.length ? messages.at(-1).sequence : null,
          next_before_sequence: messages.length > 50 ? messages.at(-50).sequence : null,
          builder_question_messages: window.questionSubmissions.length ? [] : questions });
        window.questionSubmissions = [];
        state.bridge = {
          session_context: async () => {
            if (paged) throw new Error('Full history is forbidden');
            return { messages };
          },
          ...(paged ? { session_history_page: async () => historyPage() } : {}),
          builder_chat_index: async () => [chat],
          submit_session_input: async command => {
            window.questionSubmissions.push(command);
            messages.push({ message_id: command.message_id, sequence: messages.at(-1).sequence + 1, kind: 'user', content: {
              kind: 'builder_question_answer', text: command.user_text,
              answers: command.builder_question_answers.map(answer => {
                const original = questions.find(question => question.message_id === answer.question_message_id);
                return { ...answer, tool_call_id: original.content.tool_call_id,
                  widget_id: original.content.result.widget.data.id, type: 'text',
                  question: original.content.result.widget.data.question, label: answer.value };
              }),
            } });
            return { status: 'completed' };
          },
        };
        const activity = ensureSessionActivity({ sessionId: 'question-chat', sessionRole: 'builder', campaignId: null });
        renderBuilderHistory(activity, paged ? historyPage() : { messages });
      }, paged);
      const value = '  Rowan <courier>\nKeep my exact answer.  ';
      const input = page.getByRole('textbox', { name: 'Who would you like to play?', exact: true });
      await input.fill(value);
      await input.press('Tab');
      if (paged) {
        const background = page.getByRole('textbox', { name: 'What is their background?', exact: true });
        assert.equal(await background.evaluate(element => element === document.activeElement), true);
        await background.fill('A courier from the coast.');
        await background.press('Tab');
      }
      assert.equal(await page.getByRole('button', { name: 'Submit answers', exact: true }).evaluate(
        element => element === document.activeElement), true);
      await page.keyboard.press('Space');
      const concept = page.locator('.builder-question-card').filter({ hasText: 'Who would you like to play?' });
      await concept.locator('.builder-question-answer').waitFor();
      assert.equal(await concept.locator('.builder-question-answer').textContent(), value);
      assert.equal(await page.getByRole('button', { name: 'Submit answers', exact: true }).count(), 0);
      assert.equal(await page.locator('#chronology').getByText('Here are my answers:', { exact: false }).count(), 0);
      const submissions = await page.evaluate(() => window.questionSubmissions);
      assert.equal(submissions.length, 1);
      assert.equal(submissions[0].builder_question_answers[0].value, value);
      assert.equal(submissions[0].builder_question_answers.length, paged ? 2 : 1);
      if (paged) {
        assert.equal(submissions[0].builder_question_answers[1].value, 'A courier from the coast.');
        await page.evaluate(() => window.questionQA.renderBuilderSession('question-chat'));
        assert.equal(await concept.locator('.builder-question-answer').textContent(), value);
      }
      for (const card of await page.locator('.builder-question-card').all()) {
        const box = await card.boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= width);
      }
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
}
