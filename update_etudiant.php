<?php
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Access-Control-Allow-Methods: POST, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With");

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(array("status" => "error", "message" => "Méthode non autorisée."));
    exit;
}

require_once 'admin_auth.php';
requireAdmin();
require_once 'db.php';
require_once 'student_helpers.php';

$data = json_decode(file_get_contents("php://input"), true);
if (!is_array($data)) {
    http_response_code(400);
    echo json_encode(array("status" => "error", "message" => "Corps JSON invalide."));
    exit;
}
$id = isset($data['id']) ? filter_var($data['id'], FILTER_VALIDATE_INT) : false;
if (!$id || $id < 1) {
    http_response_code(400);
    echo json_encode(array("status" => "error", "message" => "Données incomplètes."));
    exit;
}

$champs = array(
    "telephone" => "str",
    "email"     => "email",
    "sexe"      => "sexe",
    "allergies_affection" => "allergies",
);

$set = array();
$params = array(':id' => $id);

foreach ($champs as $nom => $type) {
    if (!array_key_exists($nom, $data)) continue;
    if (!is_string($data[$nom])) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => "Valeur invalide pour $nom."));
        exit;
    }

    $valeur = trim($data[$nom]);

    if ($valeur !== '' && $type === 'email' && !filter_var($valeur, FILTER_VALIDATE_EMAIL)) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => "Adresse e-mail invalide."));
        exit;
    }
    if ($type === 'sexe' && $valeur !== '' && !in_array($valeur, array('M', 'F'), true)) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => "Sexe invalide (M ou F)."));
        exit;
    }
    if ($type === 'allergies' && strlen($valeur) > 2000) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => "Le champ allergies/affections est trop long."));
        exit;
    }

    $set[] = "$nom = :$nom";
    $params[":$nom"] = $valeur === '' ? null : $valeur;
}

if (isset($data['date_naissance']) && $data['date_naissance'] !== '') {
    if (!is_string($data['date_naissance'])) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => "Date de naissance invalide."));
        exit;
    }

    $dateNaissance = parseStudentDate($data['date_naissance']);
    if (!$dateNaissance) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => "Date de naissance invalide."));
        exit;
    }

    try {
        $stmt = $pdo->prepare('SELECT matricule, nom, prenoms, date_naissance FROM etudiant WHERE id = :id');
        $stmt->execute(array(':id' => $id));
        $student = $stmt->fetch();
        if (!$student) {
            http_response_code(404);
            echo json_encode(array("status" => "error", "message" => "Étudiant introuvable."));
            exit;
        }

        $newMatricule = studentMatricule($student['nom'], $student['prenoms'], $dateNaissance);
        if ($newMatricule !== $student['matricule']) {
            $stmt = $pdo->prepare('SELECT id FROM etudiant WHERE matricule = :matricule AND id <> :id');
            $stmt->execute(array(':matricule' => $newMatricule, ':id' => $id));
            if ($stmt->fetchColumn() !== false) {
                http_response_code(409);
                echo json_encode(array("status" => "error", "message" => "Le matricule calculé existe déjà."));
                exit;
            }
            $set[] = 'matricule = :matricule';
            $params[':matricule'] = $newMatricule;
        }
        if ($dateNaissance !== $student['date_naissance']) {
            $set[] = 'date_naissance = :date_naissance';
            $params[':date_naissance'] = $dateNaissance;
        }
    } catch (InvalidArgumentException $exception) {
        http_response_code(422);
        echo json_encode(array("status" => "error", "message" => $exception->getMessage()));
        exit;
    } catch (PDOException $exception) {
        error_log('Lecture étudiant avant mise à jour UPB : ' . $exception->getMessage());
        http_response_code(500);
        echo json_encode(array("status" => "error", "message" => "Erreur lors de la lecture du dossier étudiant."));
        exit;
    }
}

if (!$set) {
    http_response_code(400);
    echo json_encode(array("status" => "error", "message" => "Données incomplètes."));
    exit;
}

try {
    $sql = "UPDATE etudiant SET " . implode(', ', $set) . " WHERE id = :id";
    $stmt = $pdo->prepare($sql);
    foreach ($params as $cle => $valeur) {
        $stmt->bindValue($cle, $valeur, $cle === ':id' ? PDO::PARAM_INT : ($valeur === null ? PDO::PARAM_NULL : PDO::PARAM_STR));
    }

    if ($stmt->execute()) {
        echo json_encode(array(
            "status" => "success",
            "message" => "Fiche étudiant mise à jour avec succès !",
            "data" => isset($newMatricule) ? array("matricule" => $newMatricule) : array()
        ), JSON_UNESCAPED_UNICODE);
    } else {
        http_response_code(500);
        echo json_encode(array("status" => "error", "message" => "Mise à jour échouée."));
    }
} catch (PDOException $e) {
    error_log('Mise à jour étudiant UPB : ' . $e->getMessage());
    http_response_code(500);
    echo json_encode(array("status" => "error", "message" => "Erreur de base de données."));
}