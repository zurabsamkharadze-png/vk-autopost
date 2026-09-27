const ALLOWED_ORIGINS = new Set([
  'https://zurabsamkharadze-png.github.io',
  'https://vk.com',
  'https://vk.ru'
]);

const ALLOWED_IMAGE_HOSTS = new Set([
  'cdn.tripster.ru',
  'resize.tripster.ru'
]);

function isAllowedVkHost(hostname) {
  const h = hostname.toLowerCase();
  return h === 'vk.com' || h.endsWith('.vk.com') ||
         h === 'vk.ru' || h.endsWith('.vk.ru') ||
         h === 'userapi.com' || h.endsWith('.userapi.com') ||
         h === 'userapi.ru' || h.endsWith('.userapi.ru') ||
         h === 'vkuserphoto.ru' || h.endsWith('.vkuserphoto.ru');
}

function applyCors(req, res) {
  const origin = req.headers.origin;
  if (origin && ALLOWED_ORIGINS.has(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 'no-store');
}

export default async function handler(req, res) {
  applyCors(req, res);

  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const { upload_url, image_url } = req.body || {};
    if (!upload_url || !image_url) {
      return res.status(400).json({ error: 'upload_url and image_url are required' });
    }

    let uploadUrl;
    let imageUrl;
    try {
      uploadUrl = new URL(upload_url);
      imageUrl = new URL(image_url);
    } catch {
      return res.status(400).json({ error: 'Invalid URL' });
    }

    if (uploadUrl.protocol !== 'https:' || !isAllowedVkHost(uploadUrl.hostname)) {
      return res.status(400).json({ error: 'VK upload host is not allowed' });
    }
    if (imageUrl.protocol !== 'https:' || !ALLOWED_IMAGE_HOSTS.has(imageUrl.hostname.toLowerCase())) {
      return res.status(400).json({ error: 'Image host is not allowed' });
    }

    const imageResp = await fetch(imageUrl, {
      redirect: 'follow',
      headers: { 'User-Agent': 'GeoTrips-VK-Helper/1.0' }
    });
    if (!imageResp.ok) {
      return res.status(502).json({ error: `Tripster image HTTP ${imageResp.status}` });
    }

    const contentType = imageResp.headers.get('content-type') || 'image/jpeg';
    if (!contentType.toLowerCase().startsWith('image/')) {
      return res.status(400).json({ error: 'Source is not an image' });
    }

    const bytes = await imageResp.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength > 20 * 1024 * 1024) {
      return res.status(400).json({ error: 'Image size is invalid' });
    }

    const form = new FormData();
    form.append('photo', new Blob([bytes], { type: contentType }), 'tripster.jpg');

    const vkResp = await fetch(uploadUrl, {
      method: 'POST',
      body: form,
      redirect: 'follow'
    });

    const raw = await vkResp.text();
    if (!vkResp.ok) {
      return res.status(502).json({ error: `VK upload HTTP ${vkResp.status}`, detail: raw.slice(0, 500) });
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return res.status(502).json({ error: 'VK upload returned non-JSON response', detail: raw.slice(0, 500) });
    }

    return res.status(200).json(data);
  } catch (e) {
    return res.status(500).json({ error: e?.message || String(e) });
  }
}
