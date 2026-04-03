CAPTCHA Solver Module for Hermes Agent
=======================================

Solves CAPTCHAs (Cloudflare Turnstile, reCAPTCHA, hCaptcha) via:
  - CapSolver API (preferred - native Turnstile support)
  - 2Captcha API (fallback)

API Endpoints:
  CapSolver:  POST https://api.capsolver.com/createTask
              POST https://api.capsolver.com/getTaskResult
  2Captcha:   POST https://api.2captcha.com/in.php
              GET  https://api.2captcha.com/res.php

Task Types:
  CapSolver:  TurnstileTaskProxyless, ReCaptchaV2TaskProxyless
  2Captcha:   turnstile, userrecaptcha

How to Use:
  1. Get API key from https://dashboard.capsolver.com or https://2captcha.com
  2. Add to ~/.hermes/.env:
       CAPSOLVER_API_KEY=CAPxxxx
       TWO_CAPTCHA_API_KEY=xxxx
  3. The agent will use this module automatically when it encounters
     CAPTCHAs during browser automation.

How It Works:
  1. When a script encounters a Cloudflare Turnstile widget:
        sitekey: 0x4AAAAAAAc4qhUEsytXBEJx (OpenRouter)
  2. The captcha_solver sends sitekey + pageURL to the provider API
  3. Provider uses real browsers with residential IPs to solve the CAPTCHA
  4. Provider returns a solution token
  5. The token is injected into the page's hidden form field via JS
  6. The form is submitted successfully

Token Injection:
  For Turnstile: document.querySelector('textarea[name="cf-turnstile-response"]').value = token
  For reCAPTCHA:  document.querySelector('textarea[name="g-recaptcha-response"]').value = token
  Then dispatch change/input events to trigger validation.

Pricing (per 1000 CAPTCHAs):
  CapSolver:  Turnstile $0.775, reCAPTCHA v2 $0.211, hCaptcha $0.211
  2Captcha:   Turnstile $2.990, reCAPTCHA v2 $2.990, hCaptcha $2.990
  AntiCaptcha: Turnstile $1.000, reCAPTCHA v2 $2.990, hCaptcha $3.000

Note: All providers require signup with email + they have CAPTCHA on their
own signup pages. You must manually sign up once to get an API key.
