const form = document.getElementById('loginForm');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('signIn');
  const message = document.getElementById('message');
  button.disabled = true;
  message.textContent = 'Signing in…';
  try {
    const response = await fetch('/api/website-login', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: document.getElementById('username').value.trim(), password: document.getElementById('password').value}),
      signal: AbortSignal.timeout(15000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Sign-in failed. Please try again.');
    document.getElementById('password').value = '';
    window.location.replace('/');
  } catch (error) {
    message.textContent = error.name === 'TimeoutError' ? 'Sign-in timed out. Please try again.' : error.message;
    button.disabled = false;
  }
});
