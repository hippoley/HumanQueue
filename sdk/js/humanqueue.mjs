export class HumanQueue {
  constructor({baseUrl, token} = {}) {
    const envUrl = typeof process !== 'undefined' ? process.env?.HUMAN_QUEUE_URL : undefined;
    const envToken = typeof process !== 'undefined' ? process.env?.HUMAN_QUEUE_TOKEN : undefined;
    this.baseUrl = (baseUrl || envUrl || 'http://127.0.0.1:7482').replace(/\/$/, '');
    this.token = token || envToken || '';
  }

  headers(extra = {}) {
    return {
      ...extra,
      ...(this.token ? {authorization: `Bearer ${this.token}`} : {}),
    };
  }

  async ask(uri, input) {
    const response = await fetch(`${this.baseUrl}/v1/human`, {
      method: 'POST',
      headers: this.headers({'content-type': 'application/json'}),
      body: JSON.stringify({uri, ...input}),
    });
    if (!response.ok) throw new Error(`human:// request failed: ${response.status} ${await response.text()}`);
    return (await response.json()).request;
  }

  async wait(requestId, {pollMs = 1000, timeoutMs = 0} = {}) {
    const started = Date.now();
    while (true) {
      const response = await fetch(`${this.baseUrl}/v1/requests/${requestId}`, {
        headers: this.headers(),
      });
      if (!response.ok) throw new Error(`human:// wait failed: ${response.status}`);
      const {request} = await response.json();
      if (request.status === 'resolved') return request.resolution || {};
      if (['cancelled', 'expired', 'superseded'].includes(request.status)) {
        throw new Error(`human:// request ended with ${request.status}`);
      }
      if (timeoutMs && Date.now() - started >= timeoutMs) throw new Error('human:// wait timed out');
      await new Promise(resolve => setTimeout(resolve, pollMs));
    }
  }
}

// Preferred public primitive; HumanQueue remains backward compatible.
export const HumanBoundary = HumanQueue;
