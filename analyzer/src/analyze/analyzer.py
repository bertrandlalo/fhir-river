import re
import time
from collections.abc import Mapping

from analyzer.src.analyze.graphql import PyrogClient
from analyzer.src.config.logger import create_logger

from .mapping import build_squash_rules
from .analysis import Analysis
from .attribute import Attribute
from .concept_map import ConceptMap
from .cleaning_script import CleaningScript
from .merging_script import MergingScript
from .sql_column import SqlColumn
from .sql_join import SqlJoin

logger = create_logger("analyzer")


class Analyzer:
    def __init__(self, pyrog_client: PyrogClient):
        self.pyrog = pyrog_client
        # Store analyses
        # TODO think about the design here. Use http caching instead of
        # storing here, for instance?
        self.analyses: Mapping = {}
        self._cur_analysis = Analysis()
        self.last_updated_at: Mapping = {}  # store last updated timestamp for each resource_id

    def get_analysis(self, resource_mapping_id) -> Analysis:
        if resource_mapping_id not in self.analyses:
            self.fetch_analysis(resource_mapping_id)
        else:
            self.check_refresh_analysis(resource_mapping_id)
        return self.analyses[resource_mapping_id]

    def check_refresh_analysis(self, resource_mapping_id, max_seconds_refresh=3600):
        """
        This method refreshes the analyser if the last update was later than `max_seconds_refresh`
        for each resource
        """
        if time.time() - self.last_updated_at.get(resource_mapping_id) > max_seconds_refresh:
            logger.debug("Analysis too old for mapping_resource_id.")
            self.fetch_analysis(resource_mapping_id)
        else:
            logger.debug("Analysis was updated recently. Using cached analysis.")

    def fetch_analysis(self, resource_mapping_id):
        """
        Fetch mapping from API and store last updated timestamp
        :param resource_mapping_id:
        :return:
        """
        logger.debug("Fetching mapping from api.")
        resource_mapping = self.pyrog.get_resource_from_id(resource_id=resource_mapping_id)
        self.analyze(resource_mapping)
        self.last_updated_at[resource_mapping_id] = time.time()

    # TODO add an update_analysis(self, resource_mapping_id)?

    def analyze(self, resource_mapping):
        self._cur_analysis = Analysis()

        # Analyze the mapping
        self.analyze_mapping(resource_mapping)

        if not self._cur_analysis.columns:
            self._cur_analysis.is_static = True
        else:
            # Get primary key table
            self.get_primary_key(resource_mapping)

            # Add primary key to columns to fetch if needed
            self._cur_analysis.add_column(self._cur_analysis.primary_key_column)

            # Build squash rules
            self._cur_analysis.squash_rules = build_squash_rules(
                self._cur_analysis.columns,
                self._cur_analysis.joins,
                self._cur_analysis.primary_key_column.table_name(),
            )

        # Store analysis
        self.analyses[resource_mapping["id"]] = self._cur_analysis

        return self._cur_analysis

    def analyze_mapping(self, resource_mapping):
        self._cur_analysis.source_id = resource_mapping["source"]["id"]
        self._cur_analysis.resource_id = resource_mapping["id"]
        self._cur_analysis.definition = resource_mapping["definition"]
        for attribute_mapping in resource_mapping["attributes"]:
            self.analyze_attribute(attribute_mapping)

        return self._cur_analysis

    def analyze_attribute(self, attribute_mapping):
        logger.debug(
            f"Analyze attribute {attribute_mapping['path']} {attribute_mapping['definitionId']}"
        )
        attribute = Attribute(
            path=attribute_mapping["path"], columns=[], static_inputs=[], merging_script=None
        )
        if not attribute_mapping["inputs"]:
            # If there are no inputs for this attribute, it means that it is an intermediary
            # attribute (ie not a leaf). It is here to give us some context information.
            # For instance, we can use it if its children attributes represent a Reference.
            if attribute_mapping["definitionId"] == "Reference":
                logger.debug(f"Analyze attribute reference !")
                # Remove trailing index
                path = re.sub(r"\[\d+\]$", "", attribute.path)
                self._cur_analysis.reference_paths.add(path)

            return

        for input in attribute_mapping["inputs"]:
            if input["sqlValue"]:
                sqlValue = input["sqlValue"]
                cur_col = SqlColumn(sqlValue["table"], sqlValue["column"], sqlValue["owner"])

                if input["script"]:
                    cur_col.cleaning_script = CleaningScript(input["script"])

                if input["conceptMapId"]:
                    cur_col.concept_map = ConceptMap(input["conceptMapId"])

                for join in sqlValue["joins"]:
                    tables = join["tables"]
                    left = SqlColumn(tables[0]["table"], tables[0]["column"], tables[0]["owner"])
                    right = SqlColumn(tables[1]["table"], tables[1]["column"], tables[1]["owner"])
                    self._cur_analysis.add_join(SqlJoin(left, right))

                self._cur_analysis.add_column(cur_col)
                attribute.add_column(cur_col)

            elif input["staticValue"]:
                attribute.add_static_input(input["staticValue"])

        if attribute_mapping["mergingScript"]:
            attribute.merging_script = MergingScript(attribute_mapping["mergingScript"])

        self._cur_analysis.attributes.append(attribute)

    def get_primary_key(self, resource_mapping):
        """ Get the primary key table and column of the provided resource.
        """
        if not resource_mapping["primaryKeyTable"] or not resource_mapping["primaryKeyColumn"]:
            raise ValueError(
                "You need to provide a primary key table and column in the mapping for "
                f"resource {resource_mapping['definitionId']}."
            )

        self._cur_analysis.primary_key_column = SqlColumn(
            resource_mapping["primaryKeyTable"],
            resource_mapping["primaryKeyColumn"],
            resource_mapping["primaryKeyOwner"],
        )

        return self._cur_analysis.primary_key_column
