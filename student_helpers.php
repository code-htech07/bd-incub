<?php

function normalizeStudentName(string $value): string
{
    $ascii = iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', trim($value));
    if ($ascii === false) {
        throw new InvalidArgumentException('Nom ou prénom invalide.');
    }

    $letters = strtoupper(preg_replace('/[^A-Za-z]/', '', $ascii));
    if ($letters === '') {
        throw new InvalidArgumentException('Nom ou prénom invalide.');
    }

    return $letters;
}

function studentMatricule(string $nom, string $prenoms, string $dateNaissance): string
{
    $date = DateTimeImmutable::createFromFormat('!Y-m-d', $dateNaissance);
    $errors = DateTimeImmutable::getLastErrors();
    if (!$date || ($errors && ($errors['warning_count'] || $errors['error_count']))
        || $date->format('Y-m-d') !== $dateNaissance
        || $date > new DateTimeImmutable('today')) {
        throw new InvalidArgumentException('Date de naissance invalide.');
    }

    $nomNormalise = normalizeStudentName($nom);
    $prenomsNormalises = normalizeStudentName($prenoms);
    return substr($nomNormalise, 0, 3)
        . substr($prenomsNormalises, 0, 1)
        . $date->format('dm')
        . $date->format('y')
        . '0001';
}

function parseStudentDate(string $value): ?string
{
    $value = trim($value);
    foreach (array('!Y-m-d', '!d/m/Y', '!d-m-Y') as $format) {
        $date = DateTimeImmutable::createFromFormat($format, $value);
        $errors = DateTimeImmutable::getLastErrors();
        if ($date && (!$errors || (!$errors['warning_count'] && !$errors['error_count']))) {
            if ($date <= new DateTimeImmutable('today')) {
                return $date->format('Y-m-d');
            }
        }
    }
    return null;
}

function lookupReferenceId(PDO $pdo, string $table, ?string $label): ?int
{
    if ($label === null || trim($label) === '') {
        return null;
    }

    if (!in_array($table, array('filiere', 'niveau'), true)) {
        throw new InvalidArgumentException('Référence invalide.');
    }

    $label = trim($label);
    $stmt = $pdo->prepare("SELECT id FROM $table WHERE LOWER(libelle) = LOWER(:libelle) LIMIT 1");
    $stmt->execute(array(':libelle' => $label));
    $id = $stmt->fetchColumn();
    if ($id !== false) {
        return (int) $id;
    }

    $stmt = $pdo->prepare("INSERT INTO $table (libelle) VALUES (:libelle) RETURNING id");
    $stmt->execute(array(':libelle' => $label));
    return (int) $stmt->fetchColumn();
}
