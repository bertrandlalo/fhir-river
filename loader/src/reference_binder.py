import re
from collections import defaultdict
from dotty_dict import dotty

from loader.src.config.logger import create_logger

logger = create_logger("reference_binder")


# dotty-dict does not handle brackets indices,
# it uses dots instead (a.0.b instead of a[0].b)
def dotty_paths(paths):
    for path in paths:
        yield re.sub(r"\[(\d+)\]", r".\1", path)


def partial_identifier(identifier):
    (
        value,
        system,
        identifier_type_code,
        identifier_type_system,
    ) = ReferenceBinder.extract_key_tuple(identifier)
    if value:
        return {"identifier.value": value, "identifier.system": system}
    else:
        return {
            "identifier.type.coding.0.code": identifier_type_code,
            "identifier.type.coding.0.system": identifier_type_system,
        }


class ReferenceBinder:
    def __init__(self, fhirstore):
        self.fhirstore = fhirstore

        # cache is a dict of form
        # {
        #   (fhir_type_target, (value, system, code, code_system)):
        #           {(fhir_type_source, path, isArray): [fhir_id1, ...]},
        #   (fhir_type_target, (value, system, code, code_system)):
        #           {(fhir_type_source, path, isArray): [fhir_id]},
        #   ...
        # }
        # eg:
        # {
        #   (Practitioner, 1234, system): {(Patient, generalPractitioner, True): [fhir-pract-id]},
        #   ...
        # }

        self.cache = defaultdict(lambda: defaultdict(list))

    def resolve_references(self, unresolved_fhir_object, reference_paths):
        fhir_object = dotty(unresolved_fhir_object)

        # iterate over the instance's references and try to resolve them
        for reference_path in dotty_paths(reference_paths):
            logger.debug(
                f"Trying to resolve reference for resource {fhir_object['id']} "
                f"at {reference_path}"
            )
            try:
                bound_ref = self.bind_existing_reference(fhir_object, reference_path)
                fhir_object[reference_path] = bound_ref
            except Exception as e:
                logger.warning(
                    "Error while binding reference for instance "
                    f"{fhir_object} at path {reference_path}: {e}"
                )

        if "identifier" in fhir_object:
            self.resolve_pending_references(fhir_object)

        return fhir_object.to_dict()

    def bind_existing_reference(self, fhir_object, reference_path):
        reference_attribute = fhir_object[reference_path]

        def bind(ref, isArray=False):
            # extract the type and itentifier of the reference
            reference_type = ref["type"]
            identifier = ref["identifier"]
            try:
                identifier_tuple = self.extract_key_tuple(identifier)
            except Exception as e:
                logger.error(e)
                return ref

            # search the referenced resource in the database
            referenced_resource = self.fhirstore.db[reference_type].find_one(
                partial_identifier(identifier), ["id"],
            )
            if referenced_resource:
                # if found, add the ID as the "literal reference"
                # (https://www.hl7.org/fhir/references-definitions.html#Reference.reference)
                logger.info(f"reference to {reference_type} {identifier} resolved")
                ref["reference"] = f"{reference_type}/{referenced_resource['id']}"
            else:
                logger.info(
                    f"caching reference to {reference_type} {identifier} at {reference_path}"
                )

                # otherwise, cache the reference to resolve it later
                target_ref = (reference_type, identifier_tuple)
                source_ref = (fhir_object["resourceType"], reference_path, isArray)
                self.cache[target_ref][source_ref].append(fhir_object["id"])
            return ref

        # If we have a list of references, we want to bind all of them.
        # Thus, we loop on all the items in reference_attribute.
        if isinstance(reference_attribute, list):
            return [bind(ref, isArray=True) for ref in reference_attribute]
        else:
            return bind(reference_attribute)

    def resolve_pending_references(self, fhir_object):
        for identifier in fhir_object["identifier"]:
            try:
                identifier_tuple = self.extract_key_tuple(identifier)
            except Exception as e:
                logger.error(e)
                continue

            target_ref = (fhir_object["resourceType"], identifier_tuple)
            pending_refs = self.cache.get(target_ref, {})
            for (source_type, reference_path, isArray), refs in pending_refs.items():
                find_predicate = {
                    "id": {"$in": refs},
                    reference_path: {"$elemMatch": partial_identifier(identifier)}
                    if isArray
                    else partial_identifier(identifier),
                }
                # handle updating reference arrays:
                # we keep the indices in the path (eg: "identifier.0.assigner.reference")
                # but if fhir_object[reference_path] is an array, we use the '$' feature of mongo
                # in order to update the right element of the array.
                # https://docs.mongodb.com/manual/reference/operator/update/positional/#update-documents-in-an-array
                # FIXME: won't work if multiple elements of the array need to be updated (see
                # https://docs.mongodb.com/manual/reference/operator/update/positional-filtered/#identifier).
                update_predicate = {
                    "$set": {
                        f"{reference_path}{'.$' if isArray else ''}.reference": fhir_object["id"]
                    }
                }
                logger.info(
                    "Updating resource %s: %s %s", source_type, find_predicate, update_predicate
                )
                self.fhirstore.db[source_type].update_many(find_predicate, update_predicate)
            if len(pending_refs) > 0:
                del self.cache[target_ref]

    @staticmethod
    def extract_key_tuple(identifier):
        """ Build a tuple that contains the essential information from an Identifier.
        This tuple serves as a map key.
        """
        value = identifier.get("value")
        system = identifier.get("system")
        identifier_type_coding = identifier["type"]["coding"][0] if "type" in identifier else {}
        identifier_type_system = identifier_type_coding.get("system")
        identifier_type_code = identifier_type_coding.get("code")

        logger.debug("%s %s", (value and system), (identifier_type_system and identifier_type_code))
        if not (bool(value and system) ^ bool(identifier_type_system and identifier_type_code)):
            raise Exception(
                f"invalid identifier: {identifier} identifier.value and identifier.system "
                "or identifier.type are required and mutually exclusive"
            )

        return (value, system, identifier_type_code, identifier_type_system)
