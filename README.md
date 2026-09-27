# VK Autopost — GeoTrips

Бесплатный автопостинг в сообщество VK `club241800139` через GitHub Actions и VK API.

## Расписание

Публикация запускается автоматически каждый день:

- 11:00 по времени Грузии
- 19:00 по времени Грузии

За один запуск публикуется один следующий пост со статусом `queued` из `posts.json`.

## Секрет

В репозитории нужно создать только один GitHub Actions secret:

`VK_ACCESS_TOKEN`

Путь: `Settings → Secrets and variables → Actions → New repository secret`.

Сам токен нельзя сохранять в коде или `posts.json`.

## Формат очереди

```json
[
  {
    "id": "unique-post-id",
    "status": "queued",
    "text": "Текст публикации",
    "image_url": "https://example.com/photo.jpg"
  }
]
```

`image_url` можно оставить пустым, если фото не нужно.

После успешной публикации GitHub Actions автоматически меняет статус на `published` и сохраняет `vk_post_id`.

## Ручной запуск

`Actions → VK Autopost → Run workflow`
