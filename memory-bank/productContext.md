# Product Context

This file provides a high-level overview of the project and the expected product that will be created. Initially it is based upon projectBrief.md (if provided) and all other available project-related information in the working directory. This file is intended to be updated as the project evolves, and should be used to inform all other modes of the project's goals and context.
2025-08-15 19:27:31 - Log of updates made will be appended as footnotes to the end of this file.

*

## Project Goal

*   Создать систему поиска и анализа судебных решений для юристов.

## Key Features

*   Прием на входе текста и сканированных документов.
*   Определение темы (фабулы) судебного разбирательства.
*   Формирование ключевых слов и дат для поиска.
*   Выполнение запросов к сайту ras.arbitr.ru.
*   Оценка релевантности найденных решений.
*   Суммаризация наиболее похожих дел и формирование вывода о судебной практике.

## Overall Architecture

*   Форк проекта open_deep_research.
*   Модуль `ras` для взаимодействия с сайтом ras.arbitr.ru.
*   Использование LangGraph для построения рабочего процесса.