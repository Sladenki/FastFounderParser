"""Поисковые кластеры под 7 проектов. Ядро идёт первым, смежные запросы — следом."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Project:
    slug: str
    title: str
    core: tuple[str, ...]
    adjacent: tuple[str, ...]


PROJECTS: tuple[Project, ...] = (
    Project(
        slug="capsuleos",
        title="CapsuleOS",
        core=(
            "AI app builder",
            "generative UI",
            "text to app",
            "конструктор приложений на ИИ",
            "софт по описанию",
            "no-code на базе LLM",
        ),
        adjacent=(
            "internal tools AI",
            "vibe coding",
            "генерация интерфейса из блоков",
            "AI software factory",
        ),
    ),
    Project(
        slug="noneradar",
        title="NoneRadar",
        core=(
            "event networking app",
            "нетворкинг на конференции",
            "QR check-in for events",
            "приложение для мероприятия без установки",
            "B2B сервис для организаторов мероприятий",
        ),
        adjacent=(
            "conference matchmaking",
            "lead capture at events",
            "нетворкинг для площадок",
            "browser-based event app",
        ),
    ),
    Project(
        slug="tourtap",
        title="TourTap",
        core=(
            "tour booking software for guides",
            "SaaS для гидов",
            "бронирование экскурсий",
            "продажа билетов в Telegram",
            "QR билет на экскурсию",
        ),
        adjacent=(
            "booking for solopreneurs",
            "запись на услугу с лимитом мест",
            "experience booking software",
            "продажа экскурсий через мессенджер",
        ),
    ),
    Project(
        slug="graphon",
        title="GraphON",
        core=(
            "hyperlocal social network",
            "campus student app",
            "городская социальная сеть",
            "студенческое приложение кампуса",
            "афиша локальных событий",
        ),
        adjacent=(
            "community platform monetization",
            "event discovery app",
            "гиперлокальное комьюнити",
            "онбординг студентов в соцсеть",
        ),
    ),
    Project(
        slug="viberoom",
        title="VibeRoom",
        core=(
            "desktop companion app",
            "virtual presence app",
            "приложение компаньон для рабочего стола",
            "виртуальное присутствие на ПК",
            "ambient co-working",
        ),
        adjacent=(
            "desktop avatar social",
            "focus together app",
            "атмосфера для работы за компьютером",
            "social desktop widget",
        ),
    ),
    Project(
        slug="morthdesktop",
        title="MorthDesktop",
        core=(
            "window manager app",
            "workspace organizer desktop",
            "менеджер окон для ПК",
            "организация рабочих пространств",
            "virtual desktop manager",
        ),
        adjacent=(
            "desktop productivity utility",
            "context switching app",
            "утилита для рабочих столов Windows",
            "tiling window manager",
        ),
    ),
    Project(
        slug="homiesjarvis",
        title="HomiesJarvis",
        core=(
            "smart home AI assistant",
            "умный дом с голосовым ассистентом",
            "local AI home assistant",
            "автоматизация умного дома",
            "personal AI assistant for home",
        ),
        adjacent=(
            "voice assistant IoT",
            "home automation subscription",
            "локальный голосовой ассистент",
            "AI smart home platform",
        ),
    ),
)


def select_projects(slugs: list[str] | None) -> list[Project]:
    if not slugs:
        return list(PROJECTS)
    wanted = {item.strip().lower() for item in slugs}
    chosen = [project for project in PROJECTS if project.slug in wanted or project.title.lower() in wanted]
    unknown = wanted - {project.slug for project in chosen} - {project.title.lower() for project in chosen}
    if unknown:
        known = ", ".join(project.slug for project in PROJECTS)
        raise SystemExit(f"Неизвестный проект: {', '.join(sorted(unknown))}. Доступны: {known}")
    return chosen
