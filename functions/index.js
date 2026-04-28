const { onCall, HttpsError } = require('firebase-functions/v2/https');
const { defineSecret } = require('firebase-functions/params');

const gptApiKey = defineSecret('GPT_API_KEY');

exports.ocrEnvelope = onCall({ secrets: [gptApiKey], region: 'asia-east1' }, async (request) => {
  if (!request.auth) {
    throw new HttpsError('unauthenticated', '請先登入');
  }

  const { imageBase64, mimeType, prompt } = request.data;

  if (!imageBase64 || !mimeType) {
    throw new HttpsError('invalid-argument', '缺少圖片資料');
  }

  const ocrPrompt = prompt || '請從圖片中辨識文字，以純 JSON 格式回傳。找不到的欄位填空字串。';

  const response = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${gptApiKey.value()}`
    },
    body: JSON.stringify({
      model: 'gpt-4o',
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: ocrPrompt },
          { type: 'image_url', image_url: { url: `data:${mimeType};base64,${imageBase64}`, detail: 'high' } }
        ]
      }],
      max_tokens: 400
    })
  });

  const data = await response.json();
  if (!response.ok) throw new HttpsError('internal', data.error?.message || 'GPT 辨識失敗');

  const content = data.choices[0].message.content.trim();
  const match = content.match(/\{[\s\S]*\}/);
  if (!match) throw new HttpsError('internal', '無法解析辨識結果');

  return JSON.parse(match[0]);
});
