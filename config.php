<?php
// Configuration de l'API Concours Génie UPB

return [
    // Surcharge via variables d'environnement
    'db' => [
        'host'   => getenv('UPB_DB_HOST')   ?: 'localhost',
        'dbname' => getenv('UPB_DB_NAME')   ?: 'upb_concours',
        'user'   => getenv('UPB_DB_USER')   ?: 'postgres',
        'pass'   => getenv('UPB_DB_PASS')   ?: '@zerty123',
        'driver' => getenv('UPB_DB_DRIVER') ?: 'pgsql',
    ],

    // L'espace administrateur requiert UPB_ADMIN_TOKEN (10 caractères minimum).

    // Pagination par défaut
    'default_per_page' => 20,
    'max_per_page'     => 100,
];
