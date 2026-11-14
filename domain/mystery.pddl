(define (domain dizal)
  (:requirements :strips :typing)
  (:types maga zude lubud fosap)
  (:predicates
    (burobez ?dika - maga ?baviz - zude)
    (dubugo ?dika - maga ?gamos - zude ?lakaved - zude)
    (kofuk ?dika - maga ?fopog - lubud)
    (fudov ?latip - lubud ?nami - lubud)
    (zedurep ?fopog - lubud)
    (dukul ?fopog - lubud)
    (sogef ?baviz - zude)
    (vinu ?kavetif - fosap ?baviz - zude)
    (keve ?baviz - zude)
    (deno ?baviz - zude)
    (zokosin ?baviz - zude)
    (foma ?dika - maga)
    (niraduv ?dika - maga)
    (zozum ?dika - maga)
    (nuvoda ?dika - maga ?baviz - zude)
    (vuril ?dika - maga ?baviz - zude)
    (femo ?baviz - zude)
    (tabi ?baviz - zude))

  (:action kibel
    :parameters (?dika - maga ?gamos - zude ?lakaved - zude ?latip - lubud ?nami - lubud)
    :precondition (and (burobez ?dika ?gamos) (dubugo ?dika ?gamos ?lakaved)
                       (kofuk ?dika ?latip) (fudov ?latip ?nami))
    :effect (and (burobez ?dika ?lakaved) (not (burobez ?dika ?gamos))
                 (kofuk ?dika ?nami) (not (kofuk ?dika ?latip))))

  (:action rifuraz
    :parameters (?dika - maga ?baviz - zude ?fopog - lubud ?pade - lubud ?kavetif - fosap)
    :precondition (and (burobez ?dika ?baviz) (sogef ?baviz) (vinu ?kavetif ?baviz) (kofuk ?dika ?fopog)
                       (zedurep ?fopog) (dukul ?pade))
    :effect (and (not (kofuk ?dika ?fopog)) (kofuk ?dika ?pade) (not (vinu ?kavetif ?baviz))))

  (:action sebo
    :parameters (?dika - maga ?baviz - zude ?latip - lubud ?nami - lubud)
    :precondition (and (burobez ?dika ?baviz) (deno ?baviz) (foma ?dika)
                       (zozum ?dika) (kofuk ?dika ?latip) (fudov ?latip ?nami))
    :effect (and (nuvoda ?dika ?baviz) (not (zozum ?dika))
                 (kofuk ?dika ?nami) (not (kofuk ?dika ?latip))))

  (:action rada
    :parameters (?dika - maga ?baviz - zude)
    :precondition (and (burobez ?dika ?baviz) (zokosin ?baviz) (niraduv ?dika))
    :effect (vuril ?dika ?baviz))

  (:action beded
    :parameters (?dika - maga ?kezo - zude ?baviz - zude)
    :precondition (and (nuvoda ?dika ?kezo) (burobez ?dika ?baviz) (keve ?baviz))
    :effect (and (femo ?kezo) (zozum ?dika)
                 (not (nuvoda ?dika ?kezo))))

  (:action vumomos
    :parameters (?dika - maga ?zimepu - zude ?baviz - zude)
    :precondition (and (vuril ?dika ?zimepu) (burobez ?dika ?baviz) (keve ?baviz))
    :effect (and (tabi ?zimepu) (not (vuril ?dika ?zimepu)))))
